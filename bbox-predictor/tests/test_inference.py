"""Geometry and failure tests use deterministic detectors, never annotations."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from bbox_detector.aggregation import Detection, RegionEstimate, aggregate_detections
from bbox_detector.common import CLASS_NAMES, document_tiles
from bbox_detector.inference import InferenceError, RawDetection, UltralyticsDetector, predict_document


class GeometricDetector:
    """Project fixed native-coordinate boxes into each expected source crop."""

    def __init__(self, tiles, boxes, *, skipped=None):
        self.tiles = list(tiles)
        self.boxes = boxes
        self.skipped = skipped or set()
        self.calls = 0

    def predict(self, image, *, imgsz, confidence, device):
        assert image.mode == "RGB"
        assert image.width == imgsz
        tile = self.tiles[self.calls]
        call = self.calls
        self.calls += 1
        sx = image.width / (tile[2] - tile[0])
        sy = image.height / (tile[3] - tile[1])
        for class_id, native in self.boxes.items():
            if (call, class_id) in self.skipped:
                continue
            x1, y1 = max(native[0], tile[0]), max(native[1], tile[1])
            x2, y2 = min(native[2], tile[2]), min(native[3], tile[3])
            if x2 > x1 and y2 > y1:
                yield RawDetection(class_id, ((x1 - tile[0]) * sx, (y1 - tile[1]) * sy, (x2 - tile[0]) * sx, (y2 - tile[1]) * sy), 0.9)


def _image(tmp_path, size=(100, 250), mode="RGB"):
    path = tmp_path / "scan.tif"
    image = Image.new(mode, size)
    if mode == "P":
        image.putpalette([value for value in range(256) for _ in range(3)])
    image.save(path)
    return path


def _regions(height=250):
    return {0: (10, 20, 20, height - 20), 1: (40, 20, 50, height - 20), 2: (25, 2, 85, 15)}


@pytest.mark.parametrize("mode", ["RGB", "P", "1"])
def test_modes_stream_and_inverse_coordinates(tmp_path, mode):
    image = _image(tmp_path, mode=mode)
    boxes = _regions()
    detector = GeometricDetector(document_tiles(100, 250), boxes)
    diagnostics = {}
    result = predict_document(image, detector=detector, imgsz=64, diagnostics=diagnostics)
    assert result == {"image_name": image.name, "width": 100, "height": 250, "bounding_boxes": {CLASS_NAMES[index]: list(box) for index, box in boxes.items()}}
    assert diagnostics["scan_tiles"] == 4
    assert diagnostics["retry_tiles"] == 0


def test_short_document_and_page_edges(tmp_path):
    image = _image(tmp_path, size=(100, 60))
    boxes = {0: (10, 0, 20, 60), 1: (40, 0, 50, 60), 2: (25, 4, 85, 18)}
    result = predict_document(image, detector=GeometricDetector(document_tiles(100, 60), boxes), imgsz=64)
    assert result["bounding_boxes"] == {CLASS_NAMES[index]: list(box) for index, box in boxes.items()}


def test_cross_class_overlap_is_preserved(tmp_path):
    image = _image(tmp_path)
    boxes = _regions()
    boxes[1] = (12, 20, 24, 230)
    result = predict_document(image, detector=GeometricDetector(document_tiles(100, 250), boxes), imgsz=64)
    assert result["bounding_boxes"][CLASS_NAMES[0]] == [10, 20, 20, 230]
    assert result["bounding_boxes"][CLASS_NAMES[1]] == [12, 20, 24, 230]


def test_columns_share_union_of_vertical_extents_and_keep_independent_x(tmp_path):
    image = _image(tmp_path)
    boxes = _regions()
    boxes[0] = (10, 30, 20, 210)
    boxes[1] = (40, 20, 50, 230)
    diagnostics = {}
    result = predict_document(
        image,
        detector=GeometricDetector(document_tiles(100, 250), boxes),
        imgsz=64,
        diagnostics=diagnostics,
    )
    assert result["bounding_boxes"][CLASS_NAMES[0]] == [10, 20, 20, 230]
    assert result["bounding_boxes"][CLASS_NAMES[1]] == [40, 20, 50, 230]
    assert diagnostics["shared_column_vertical_extent"] == {
        "applied": True,
        "top": 20,
        "bottom": 230,
        "original_boxes": {
            CLASS_NAMES[0]: [10, 30, 20, 210],
            CLASS_NAMES[1]: [40, 20, 50, 230],
        },
    }


def test_censored_boundaries_recovered_from_overlapping_interiors(tmp_path):
    image = _image(tmp_path, size=(100, 350))
    boxes = {0: (10, 50, 20, 300), 1: (40, 50, 50, 300), 2: (25, 5, 85, 20)}
    result = predict_document(image, detector=GeometricDetector(document_tiles(100, 350), boxes), imgsz=64)
    assert result["bounding_boxes"][CLASS_NAMES[0]] == [10, 50, 20, 300]


def test_recentered_retry_resolves_endpoint(tmp_path):
    image = _image(tmp_path)
    tiles = document_tiles(100, 250)
    boxes = _regions()
    boxes[0] = (10, 70, 20, 230)

    class AmbiguousDetector(GeometricDetector):
        def predict(self, image, *, imgsz, confidence, device):
            call = self.calls
            items = list(super().predict(image, imgsz=imgsz, confidence=confidence, device=device))
            for item in items:
                if call == 1 and item.class_id == 0:
                    yield RawDetection(0, (item.box[0], 0, item.box[2], item.box[3]), item.confidence)
                else:
                    yield item

    detector = AmbiguousDetector(tiles + [(0, 25, 100, 125)], boxes, skipped={(0, 0)})
    diagnostics = {}
    result = predict_document(image, detector=detector, imgsz=64, diagnostics=diagnostics)
    assert result["bounding_boxes"][CLASS_NAMES[0]] == [10, 20, 20, 230]
    assert diagnostics["shared_column_vertical_extent"]["original_boxes"][CLASS_NAMES[0]] == [10, 70, 20, 230]
    assert diagnostics["retry_tiles"] == 1


def test_legend_retries_when_first_grid_only_sees_partial_boxes(tmp_path):
    image = _image(tmp_path)
    tiles = document_tiles(100, 250)
    boxes = _regions()
    boxes[2] = (25, 80, 85, 110)

    class PartialLegendDetector(GeometricDetector):
        def predict(self, image, *, imgsz, confidence, device):
            call = self.calls
            items = list(super().predict(image, imgsz=imgsz, confidence=confidence, device=device))
            for item in items:
                if call == 1 and item.class_id == 2:
                    continue
                else:
                    yield item

    # On tile zero the legend is cut at y=100; its center estimate is y=90,
    # so the retry starts at y=40 and contains the complete legend.
    detector = PartialLegendDetector(tiles + [(0, 40, 100, 140)], boxes, skipped={(2, 2)})
    result = predict_document(image, detector=detector, imgsz=64)
    assert result["bounding_boxes"][CLASS_NAMES[2]] == [25, 80, 85, 110]


def test_longest_horizontal_consensus_rejects_spurious_tiles_and_gaps():
    items = []
    for index, top in enumerate(range(0, 400, 50)):
        items.append(Detection(0, (10, max(20, top), 20, min(430, top + 100)), 0.9, (0, top, 100, top + 100), index))
    items.extend([
        Detection(0, (70, 50, 90, 150), 0.99, (0, 50, 100, 150), 20),
        Detection(0, (10, 700, 20, 790), 0.99, (0, 700, 100, 800), 21),
    ])
    result = aggregate_detections(items, 100, 1000)[CLASS_NAMES[0]]
    assert result.resolved
    assert result.box == (10, 20, 20, 430)


def test_conflicting_endpoint_subsets_keep_run_extent_for_retry():
    items = [
        Detection(0, (10, 100, 20, 118), 0.9, (0, 100, 100, 200), 0),
        Detection(0, (10, 120, 20, 140), 0.9, (0, 40, 100, 140), 1),
    ]
    estimate = aggregate_detections(items, 100, 300)[CLASS_NAMES[0]]
    assert not estimate.resolved
    assert estimate.box == (10, 100, 20, 140)
    assert estimate.missing_boundaries == ("top", "bottom")


def test_final_guard_rejects_empty_box_as_explicit_inference_error(tmp_path, monkeypatch):
    import bbox_detector.inference as inference

    image = _image(tmp_path)
    estimates = {name: RegionEstimate(box) for name, box in zip(CLASS_NAMES, _regions().values())}
    estimates[CLASS_NAMES[0]] = RegionEstimate((10, 50, 20, 50))
    monkeypatch.setattr(inference, "aggregate_detections", lambda *args: estimates)
    detector = GeometricDetector(document_tiles(100, 250), _regions())
    with pytest.raises(InferenceError, match="invalid_box") as caught:
        predict_document(image, detector=detector, imgsz=64)
    assert CLASS_NAMES[0] not in caught.value.partial_boxes
    assert caught.value.diagnostics["unresolved"] == {CLASS_NAMES[0]: ["invalid_box"]}


def test_missing_class_fails_explicitly_with_partial_boxes(tmp_path):
    image = _image(tmp_path)
    boxes = _regions()
    del boxes[2]
    tiles = document_tiles(100, 250)
    detector = GeometricDetector(tiles + [(0, 25, 100, 125)], boxes)
    with pytest.raises(InferenceError, match="lithology_legend") as caught:
        predict_document(image, detector=detector, imgsz=64)
    assert set(caught.value.partial_boxes) == set(CLASS_NAMES[:2])
    assert caught.value.diagnostics["unresolved"] == {"lithology_legend": ["region"]}


def test_unresolved_censored_boundary_does_not_invent_endpoint(tmp_path):
    image = _image(tmp_path, size=(100, 350))
    tiles = document_tiles(100, 350)
    boxes = {0: (10, 50, 20, 300), 1: (40, 20, 50, 330), 2: (25, 5, 85, 20)}
    # Omit the two windows which could observe the true top in their interior.
    skipped = {(0, 0), (1, 0)}

    class AlwaysCensored(GeometricDetector):
        def predict(self, image, *, imgsz, confidence, device):
            items = list(super().predict(image, imgsz=imgsz, confidence=confidence, device=device))
            for item in items:
                if item.class_id == 0:
                    yield RawDetection(0, (item.box[0], 0, item.box[2], item.box[3]), item.confidence)
                else:
                    yield item

    detector = AlwaysCensored(tiles + [(0, 75, 100, 175)], boxes, skipped=skipped)
    with pytest.raises(InferenceError, match="depth_column"):
        predict_document(image, detector=detector, imgsz=64)


def test_unknown_class_id_rejected(tmp_path):
    image = _image(tmp_path)

    class WrongDetector:
        def predict(self, image, **kwargs):
            yield RawDetection(3, (1, 1, 4, 4))

    with pytest.raises(ValueError, match="unknown class"):
        predict_document(image, detector=WrongDetector(), imgsz=64)


def test_pretrained_coco_class_names_rejected_without_torch(tmp_path, monkeypatch):
    weights = tmp_path / "best.pt"
    weights.write_bytes(b"fake weights")
    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=lambda path: SimpleNamespace(names={0: "person"})))
    with pytest.raises(ValueError, match="class IDs"):
        UltralyticsDetector(weights)


def test_broken_torch_install_has_actionable_error(tmp_path, monkeypatch):
    weights = tmp_path / "best.pt"
    weights.write_bytes(b"fake weights")

    def broken_model(path):
        raise OSError("libcudart.so cannot be opened")

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=broken_model))
    with pytest.raises(RuntimeError, match="clean CPU environment"):
        UltralyticsDetector(weights)


def test_cli_writes_only_complete_json(tmp_path, monkeypatch):
    import predict_bboxes

    output = tmp_path / "result.json"
    payload = {"image_name": "scan.tif", "width": 100, "height": 250, "bounding_boxes": {CLASS_NAMES[index]: list(box) for index, box in _regions().items()}}
    monkeypatch.setattr(predict_bboxes, "predict_document", lambda *args, **kwargs: payload)
    arguments = ["--image", "scan.tif", "--weights", "best.pt", "--output", str(output)]
    assert predict_bboxes.main(arguments) == 0
    assert json.loads(output.read_text()) == payload

    def fail(*args, **kwargs):
        raise InferenceError("missing legend")

    monkeypatch.setattr(predict_bboxes, "predict_document", fail)
    failed_output = tmp_path / "failed.json"
    arguments[-1] = str(failed_output)
    assert predict_bboxes.main(arguments) == 2
    assert not failed_output.exists()


def test_cli_rejects_output_alias_of_source(tmp_path):
    import predict_bboxes

    image = _image(tmp_path)
    original = image.read_bytes()
    assert predict_bboxes.main(["--image", str(image), "--weights", "best.pt", "--output", str(image)]) == 2
    assert image.read_bytes() == original
