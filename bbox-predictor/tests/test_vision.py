"""Classical geometry is tested without model weights or annotation files."""

from __future__ import annotations

from contextlib import contextmanager

import numpy as np
import pytest
from PIL import Image, ImageDraw

from bbox_detector.common import open_scan
from bbox_detector.vision import horizontal_lines, read_gray, refine_prediction, vertical_lines


def _fixture(tmp_path, *, lines=(50, 104), color=0, boxes=None, size=(200, 1000)):
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    for x in lines:
        draw.line((x, 0, x, size[1] - 1), fill=(color, color, color), width=1)
    path = tmp_path / "unseen_scan.tif"
    image.save(path)
    payload = {"image_name": path.name, "width": size[0], "height": size[1], "bounding_boxes": boxes or {"depth_column": [48, 50, 102, 950]}}
    return path, payload


@pytest.mark.parametrize("color", [0, 223, 240])
def test_multithreshold_rules_include_faint_straight_lines(color):
    gray = np.full((160, 100), 255, dtype=np.uint8)
    gray[:, 22] = color
    gray[:, 71:73] = color
    lines = vertical_lines(gray)
    assert [(line.start, line.end) for line in lines] == [(22, 23), (71, 73)]
    assert all(line.votes >= 2 and line.support >= 0.95 for line in lines)
    horizontal = horizontal_lines(gray.T.copy())
    assert [(line.start, line.end) for line in horizontal] == [(22, 23), (71, 73)]


def test_broad_dark_fill_is_not_a_narrow_rule():
    gray = np.full((160, 100), 255, dtype=np.uint8)
    gray[:, 30:60] = 0
    assert vertical_lines(gray) == []


def test_read_gray_preserves_requested_native_crop_and_resize(tmp_path):
    path, _ = _fixture(tmp_path)
    with open_scan(path) as scan:
        native = read_gray(scan, (45, 100, 60, 160))
        resized = read_gray(scan, (45, 100, 60, 160), size=(30, 120))
    assert native.shape == (60, 15)
    assert native.dtype == np.uint8
    assert np.all(native[:, 5] == 0)
    assert resized.shape == (120, 30)


@pytest.mark.parametrize("color", [0, 223])
def test_refines_both_edges_with_native_exclusive_coordinates(tmp_path, color):
    path, payload = _fixture(tmp_path, color=color)
    original = payload["bounding_boxes"]["depth_column"][:]
    diagnostics = {}
    result = refine_prediction(path, payload, diagnostics=diagnostics)
    assert result["bounding_boxes"]["depth_column"] == [50, 50, 105, 950]
    assert payload["bounding_boxes"]["depth_column"] == original
    assert diagnostics["refined_regions"] == 1
    assert diagnostics["sample_crops"] == 3
    assert diagnostics["regions"]["depth_column"]["edges"]["left"]["support_samples"] == 3


def test_missing_classes_and_legend_are_preserved(tmp_path):
    path, payload = _fixture(tmp_path, boxes={"lithology_legend": [30, 30, 150, 150]})
    diagnostics = {}
    result = refine_prediction(path, payload, diagnostics=diagnostics)
    assert result == payload
    assert set(result["bounding_boxes"]) == {"lithology_legend"}
    assert diagnostics["regions"]["lithology_legend"]["reason"] == "legend_boundary_not_verified"


def test_blank_region_is_unchanged(tmp_path):
    path, payload = _fixture(tmp_path, lines=())
    diagnostics = {}
    assert refine_prediction(path, payload, diagnostics=diagnostics) == payload
    assert diagnostics["regions"]["depth_column"]["edges"]["left"]["reason"] == "insufficient_line_support"


def test_competing_nearby_grid_rule_rejects_refinement(tmp_path):
    path, payload = _fixture(tmp_path, lines=(50, 54, 104))
    diagnostics = {}
    assert refine_prediction(path, payload, diagnostics=diagnostics) == payload
    left = diagnostics["regions"]["depth_column"]["edges"]["left"]
    assert left["reason"] == "ambiguous_parallel_rules"
    assert left["candidates"] == [50, 54]


def test_rules_beyond_bounded_movement_do_not_change_box(tmp_path):
    path, payload = _fixture(tmp_path, lines=(61, 115))
    diagnostics = {}
    assert refine_prediction(path, payload, max_shift_px=4, diagnostics=diagnostics) == payload
    assert diagnostics["refined_regions"] == 0


def test_one_supported_edge_is_insufficient(tmp_path):
    path, payload = _fixture(tmp_path, lines=(50,))
    diagnostics = {}
    assert refine_prediction(path, payload, diagnostics=diagnostics) == payload
    assert not diagnostics["regions"]["depth_column"]["edges"]["left"]["accepted"]


def test_inward_snapping_preserves_possible_intentional_padding(tmp_path):
    path, payload = _fixture(tmp_path, lines=(51, 98), boxes={"depth_column": [48, 50, 102, 950]})
    diagnostics = {}
    assert refine_prediction(path, payload, diagnostics=diagnostics) == payload
    assert diagnostics["regions"]["depth_column"]["reason"] == "possible_intentional_padding"


def test_independent_regions_preserve_cross_class_overlap(tmp_path):
    path, payload = _fixture(tmp_path, boxes={"depth_column": [48, 50, 102, 950], "interpreted_lithology_column": [49, 50, 103, 950]})
    result = refine_prediction(path, payload)
    assert result["bounding_boxes"]["depth_column"] == [50, 50, 105, 950]
    assert result["bounding_boxes"]["interpreted_lithology_column"] == [50, 50, 105, 950]


def test_single_strip_evidence_cannot_refine(tmp_path):
    path, payload = _fixture(tmp_path, lines=())
    with Image.open(path) as source:
        image = source.copy()
    draw = ImageDraw.Draw(image)
    for x in (50, 104):
        draw.line((x, 150, x, 300), fill="black")
    image.save(path)
    assert refine_prediction(path, payload) == payload


def test_huge_document_only_requests_bounded_body_crops(monkeypatch):
    import bbox_detector.vision as vision

    calls = []

    class Scan:
        size = (5000, 400000)

    @contextmanager
    def fake_open(path):
        assert str(path) == "huge_scan.tif"
        yield Scan()

    def fake_gray(scan, box, size=None):
        calls.append(box)
        assert box[3] - box[1] <= 512
        assert box[2] - box[0] <= 100
        assert size is None
        array = np.full((box[3] - box[1], box[2] - box[0]), 255, dtype=np.uint8)
        array[:, 1000 - box[0]] = 0
        array[:, 1054 - box[0]] = 0
        return array

    monkeypatch.setattr(vision, "open_scan", fake_open)
    monkeypatch.setattr(vision, "read_gray", fake_gray)
    payload = {"image_name": "huge_scan.tif", "width": 5000, "height": 400000, "bounding_boxes": {"depth_column": [998, 10000, 1052, 390000]}}
    result = refine_prediction("huge_scan.tif", payload)
    assert len(calls) == 3
    assert result["bounding_boxes"]["depth_column"] == [1000, 10000, 1055, 390000]


@pytest.mark.parametrize("box", [[0, 20, 0, 100], [100, 20, 50, 100], [-1, 20, 50, 100], [10.5, 20, 50, 100]])
def test_invalid_native_box_rejected(tmp_path, box):
    path, payload = _fixture(tmp_path, boxes={"depth_column": box})
    with pytest.raises(ValueError, match="Invalid native-coordinate box"):
        refine_prediction(path, payload)


def test_header_dimensions_must_match_original(tmp_path):
    path, payload = _fixture(tmp_path)
    payload["height"] = 50
    with pytest.raises(ValueError, match="dimensions"):
        refine_prediction(path, payload)
