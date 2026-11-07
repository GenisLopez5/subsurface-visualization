import pytest
from PIL import Image

from bbox_detector.common import CLASS_NAMES
from bbox_detector.inference import InferenceError, RawDetection
from bbox_detector.legend_inference import (
    fuse_legend_prediction, merge_aligned_legend_fragments, predict_legend,
)


class FixedLegendDetector:
    def __init__(self, detections):
        self.detections = detections
        self.calls = 0

    def predict(self, image, **_options):
        self.calls += 1
        assert image.size == (64, 64)
        yield from self.detections


def _base(path):
    return {
        "image_name": path.name,
        "width": 100,
        "height": 300,
        "bounding_boxes": {
            CLASS_NAMES[0]: [10, 60, 20, 270],
            CLASS_NAMES[1]: [40, 50, 55, 280],
            CLASS_NAMES[2]: [5, 5, 90, 30],
        },
    }


def test_header_specialist_replaces_legend_and_shares_column_y(tmp_path):
    path = tmp_path / "scan.tif"
    Image.new("RGB", (100, 300), "white").save(path)
    raw = RawDetection(0, (12.8, 6.4, 51.2, 25.6), 0.9)
    detector = FixedLegendDetector([raw])
    diagnostics = {}
    result = fuse_legend_prediction(
        path,
        _base(path),
        detector=detector,
        imgsz=64,
        confidence=0.1,
        diagnostics=diagnostics,
    )
    assert result["bounding_boxes"][CLASS_NAMES[0]] == [10, 50, 20, 280]
    assert result["bounding_boxes"][CLASS_NAMES[1]] == [40, 50, 55, 280]
    assert result["bounding_boxes"][CLASS_NAMES[2]] == [20, 10, 80, 40]
    assert diagnostics["legend_specialist"]["candidate_count"] == 1


def test_missing_specialist_candidate_preserves_existing_base_legend(tmp_path):
    path = tmp_path / "scan.tif"
    Image.new("RGB", (100, 300), "white").save(path)
    result = fuse_legend_prediction(path, _base(path), detector=FixedLegendDetector([]), imgsz=64)
    assert result["bounding_boxes"][CLASS_NAMES[2]] == [5, 5, 90, 30]


def test_missing_both_legends_fails_with_column_partials(tmp_path):
    path = tmp_path / "scan.tif"
    Image.new("RGB", (100, 300), "white").save(path)
    base = _base(path)
    del base["bounding_boxes"][CLASS_NAMES[2]]
    with pytest.raises(InferenceError, match="Neither"):
        fuse_legend_prediction(path, base, detector=FixedLegendDetector([]), imgsz=64)


def test_legend_candidates_below_column_start_are_rejected(tmp_path):
    path = tmp_path / "scan.tif"
    Image.new("RGB", (100, 300), "white").save(path)
    raw = RawDetection(0, (12.8, 38.4, 51.2, 57.6), 0.9)
    with pytest.raises(InferenceError, match="no candidate"):
        predict_legend(path, column_top=50, detector=FixedLegendDetector([raw]), imgsz=64)


def test_fragment_reconstruction_joins_connected_same_row_and_rejects_distractors():
    groups = [
        {"best_box": [20, 10, 60, 40], "best_confidence": 0.8},
        {"best_box": [0, 11, 30, 41], "best_confidence": 0.6},
        {"best_box": [55, 10, 85, 40], "best_confidence": 0.5},
        # Wrong row, weak confidence, and disconnected region respectively.
        {"best_box": [0, 60, 90, 90], "best_confidence": 0.9},
        {"best_box": [80, 10, 100, 40], "best_confidence": 0.1},
        {"best_box": [95, 10, 100, 40], "best_confidence": 0.6},
    ]
    assert merge_aligned_legend_fragments(groups, width=100, height=100) == [0, 10, 85, 41]


def test_fragment_fusion_is_opt_in_and_keeps_column_coordinates(tmp_path):
    path = tmp_path / "scan.tif"
    Image.new("RGB", (100, 300), "white").save(path)
    detections = [
        RawDetection(0, (12.8, 6.4, 38.4, 25.6), 0.9),
        RawDetection(0, (32, 6.4, 57.6, 25.6), 0.7),
    ]
    base = _base(path)
    unchanged = fuse_legend_prediction(path, base, detector=FixedLegendDetector(detections), imgsz=64)
    merged = fuse_legend_prediction(
        path, base, detector=FixedLegendDetector(detections), imgsz=64, merge_fragments=True,
    )
    assert unchanged["bounding_boxes"][CLASS_NAMES[2]] == [20, 10, 60, 40]
    assert merged["bounding_boxes"][CLASS_NAMES[2]] == [20, 10, 90, 40]
    for name in CLASS_NAMES[:2]:
        assert merged["bounding_boxes"][name] == unchanged["bounding_boxes"][name]
    assert base == _base(path)
