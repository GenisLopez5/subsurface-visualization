import json
import shutil

import cv2
import numpy as np
import pytest
from PIL import Image

from bbox_detector.classical import ClassicalDetector, _project_box, build_template_bank
from bbox_detector.common import CLASS_NAMES
from bbox_detector.inference import InferenceError


def make_scan(path, body_height=1200, seed=8):
    width, header_height, footer_height = 400, 400, 250
    rng = np.random.default_rng(seed)
    header = np.full((header_height, width, 3), 255, np.uint8)
    footer = np.full((footer_height, width, 3), 255, np.uint8)
    for bitmap, tag in ((header, "HEADER"), (footer, "FOOTER")):
        for index in range(90):
            x, y = int(rng.integers(5, 350)), int(rng.integers(20, bitmap.shape[0] - 10))
            cv2.putText(bitmap, f"{tag[0]}{index}", (x, y), cv2.FONT_HERSHEY_SIMPLEX, .25, (0, 0, 0), 1)
    body = np.full((body_height, width, 3), 255, np.uint8)
    for x in (60, 78, 220, 265):
        cv2.line(body, (x, 0), (x, body_height - 1), (0, 0, 0), 1)
    image = np.concatenate((header, body, footer))
    Image.fromarray(image).save(path)
    return {"image_name": path.name, "width": width, "height": image.shape[0], "bounding_boxes": {
        "depth_column": [60, 400, 78, 400 + body_height],
        "interpreted_lithology_column": [220, 400, 265, 400 + body_height],
        "lithology_legend": [25, 80, 350, 300],
    }}


def make_manifest(tmp_path):
    image = tmp_path / "train.tif"
    annotation = make_scan(image)
    annotation_path = tmp_path / "annotation.json"
    annotation_path.write_text(json.dumps(annotation))
    records = [{"image_name": image.name, "image_path": str(image), "annotation_path": str(annotation_path), "group": "train-family", "split": "train"}]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"records": records}))
    return manifest, records, annotation_path


def test_header_footer_registration_handles_changed_body_height_and_portable_bank(tmp_path):
    manifest, _, annotations = make_manifest(tmp_path)
    bank = build_template_bank(manifest, tmp_path / "bank", work_width=400, max_features=3000)
    assert bank["training_images"] == ["train.tif"]
    assert bank["references"][0]["split"] == "train"
    shutil.copytree(tmp_path / "bank", tmp_path / "portable")
    annotations.unlink()
    manifest.unlink()
    (tmp_path / "train.tif").unlink()
    query = tmp_path / "unrelated-name.tif"
    expected = make_scan(query, body_height=2400)
    details = {}
    prediction = ClassicalDetector(tmp_path / "portable").predict_document(query, diagnostics=details)
    assert tuple(prediction["bounding_boxes"]) == CLASS_NAMES
    assert prediction["height"] == expected["height"]
    for name in CLASS_NAMES:
        assert np.allclose(prediction["bounding_boxes"][name], expected["bounding_boxes"][name], atol=3)
    assert details["selected_reference"] == "train.tif"


def test_unknown_blank_layout_abstains_without_inventing_regions(tmp_path):
    manifest, _, _ = make_manifest(tmp_path)
    build_template_bank(manifest, tmp_path / "bank", work_width=400)
    query = tmp_path / "blank.tif"
    Image.new("RGB", (400, 3000), "white").save(query)
    with pytest.raises(InferenceError) as caught:
        ClassicalDetector(tmp_path / "bank").predict_document(query)
    assert caught.value.partial_boxes == {}
    assert caught.value.diagnostics["reason"] == "no_supported_layout_or_footer"


def test_missing_footer_uses_sustained_column_rules(tmp_path):
    manifest, _, _ = make_manifest(tmp_path)
    build_template_bank(manifest, tmp_path / "bank", work_width=400)
    query = tmp_path / "missing-footer.tif"
    expected = make_scan(query, body_height=3000)
    with Image.open(query) as bitmap:
        altered = np.asarray(bitmap).copy()
    altered[-250:] = 255
    Image.fromarray(altered).save(query)
    details = {}
    prediction = ClassicalDetector(tmp_path / "bank").predict_document(query, diagnostics=details)
    for name in CLASS_NAMES:
        assert np.allclose(prediction["bounding_boxes"][name], expected["bounding_boxes"][name], atol=4)
    assert details["column_end_method"] == "sustained_vertical_rules"


def test_missing_footer_and_column_rules_returns_only_supported_legend(tmp_path):
    manifest, _, _ = make_manifest(tmp_path)
    build_template_bank(manifest, tmp_path / "bank", work_width=400)
    query = tmp_path / "missing-footer-and-rules.tif"
    make_scan(query, body_height=3000)
    with Image.open(query) as bitmap:
        altered = np.asarray(bitmap).copy()
    altered[400:] = 255
    Image.fromarray(altered).save(query)
    with pytest.raises(InferenceError) as caught:
        ClassicalDetector(tmp_path / "bank").predict_document(query)
    assert set(caught.value.partial_boxes) == {"lithology_legend"}


def test_leakage_guard_rejects_reserved_references_and_shared_family(tmp_path):
    manifest, records, _ = make_manifest(tmp_path)
    with pytest.raises(ValueError, match="split='train'"):
        build_template_bank(manifest, tmp_path / "invalid", split="val")
    records.append({**records[0], "image_name": "reserved.tif", "split": "val"})
    manifest.write_text(json.dumps({"records": records}))
    with pytest.raises(ValueError, match="share a family"):
        build_template_bank(manifest, tmp_path / "invalid")


def test_bank_integrity_and_immutable_rebuild(tmp_path):
    manifest, _, _ = make_manifest(tmp_path)
    bank = build_template_bank(manifest, tmp_path / "bank", work_width=400)
    with pytest.raises(FileExistsError, match="immutable"):
        build_template_bank(manifest, tmp_path / "bank")
    feature = tmp_path / "bank" / bank["references"][0]["features_file"]
    feature.write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        ClassicalDetector(tmp_path / "bank")


def test_tiny_registration_rotation_never_extrapolates_column_x_across_body_height():
    angle = np.deg2rad(.1)
    matrix = np.asarray([[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0]])
    projections = {}
    for height in (10_000, 1_000_000):
        box = [100, 1200, 120, height - 1000]
        reference = {"sections": {
            "header": {"box": [0, 0, 1000, 5000], "work_size": [1000, 5000]},
            "footer": {"box": [0, height - 5000, 1000, height], "work_size": [1000, 5000]},
        }}
        projections[height] = {}
        for section in ("header", "footer"):
            result = _project_box(box, reference, section, matrix, reference["sections"][section], 1000)
            projections[height][section] = result
            assert 90 < result[0] < 101 and 110 < result[2] < 121
            assert result[1] == result[3]
    for section in ("header", "footer"):
        assert np.allclose(np.asarray(projections[10_000][section])[[0, 2]], np.asarray(projections[1_000_000][section])[[0, 2]])


def test_rotated_legend_encloses_all_four_transformed_corners():
    angle = np.deg2rad(2)
    matrix = np.asarray([[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0]])
    section = {"box": [0, 0, 1000, 5000], "work_size": [1000, 5000]}
    result = _project_box([100, 200, 300, 400], {"sections": {"header": section}}, "header", matrix, section, 1000, legend=True)
    corners = np.asarray([[100, 200], [300, 200], [300, 400], [100, 400]]) @ matrix[:, :2].T
    assert np.allclose(result, [corners[:, 0].min(), corners[:, 1].min(), corners[:, 0].max(), corners[:, 1].max()])
