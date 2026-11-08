from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import pytest

from bbox_detector.benchmark import benchmark_manifest, evaluate_backend, make_predictor, paired_comparison, validate_bank_split
from bbox_detector.common import CLASS_NAMES
from bbox_detector.evaluation import document_metrics
from bbox_detector.inference import InferenceError


@pytest.fixture
def prepared(tmp_path):
    boxes = dict(zip(CLASS_NAMES, ([10, 50, 20, 180], [35, 50, 55, 180], [60, 5, 95, 35])))
    records = []
    for name, group, split in (("reference.tif", "training-family", "train"), ("page0.tif", "withheld0", "val"), ("page1.tif", "withheld1", "val")):
        image = tmp_path / name
        Image.new("1", (100, 200), 1).save(image)
        annotation = tmp_path / f"{image.stem}.annotation.json"
        annotation.write_text(json.dumps({"image_name": name, "width": 100, "height": 200, "bounding_boxes": boxes}))
        records.append({"image_name": name, "image_path": str(image), "annotation_path": str(annotation), "width": 100, "height": 200, "group": group, "split": split})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"dataset_root": str(tmp_path), "records": records}))
    templates = tmp_path / "templates"
    templates.mkdir()
    bank = {"training_images": ["reference.tif"], "training_groups": ["training-family"], "source_manifest_sha256": "source-hash", "references": [{"image_name": "reference.tif", "group": "training-family", "split": "train"}]}
    (templates / "bank.json").write_text(json.dumps(bank))

    def perfect(image_path, **_kwargs):
        return {"image_name": image_path.name, "width": 100, "height": 200, "bounding_boxes": copy.deepcopy(boxes)}

    return {"root": tmp_path, "manifest": manifest, "templates": templates, "boxes": boxes, "records": records, "bank": bank, "perfect": perfect}


@pytest.mark.parametrize("kind", ["image", "family", "metadata", "split"])
def test_bank_rejects_leaked_or_incomplete_provenance(prepared, kind):
    bank = prepared["bank"]
    reference = bank["references"][0]
    if kind == "image":
        reference["image_name"] = "page0.tif"
        bank["training_images"] = ["page0.tif"]
    elif kind == "family":
        reference["group"] = "withheld1"
        bank["training_groups"] = ["withheld1"]
    elif kind == "metadata":
        bank["training_images"] = []
    else:
        reference["split"] = "val"
    (prepared["templates"] / "bank.json").write_text(json.dumps(bank))
    with pytest.raises(ValueError):
        validate_bank_split(prepared["manifest"], prepared["templates"])


def test_training_bank_cannot_be_ranked_on_its_own_training_families(prepared):
    with pytest.raises(ValueError, match="leaks"):
        benchmark_manifest(prepared["manifest"], prepared["root"] / "out", templates=prepared["templates"], split="train", save_previews=False)


def test_classical_factory_avoids_loading_yolo_and_returns_standard_schema(prepared):
    class FakeClassical:
        def __init__(self, bank):
            assert bank == prepared["templates"]

        def predict_document(self, image_path, *, diagnostics=None):
            diagnostics.update(selected_reference="reference.tif")
            return prepared["perfect"](image_path)

    with patch("bbox_detector.classical.ClassicalDetector", FakeClassical), patch("bbox_detector.inference.UltralyticsDetector", side_effect=AssertionError("YOLO must not load")):
        predict = make_predictor("classical", templates=prepared["templates"])
        details = {}
        prediction = predict(prepared["root"] / "page0.tif", diagnostics=details, weights=None, device="cpu", imgsz=64, confidence=0.25)
    assert set(prediction) == {"image_name", "width", "height", "bounding_boxes"}
    assert details["selected_reference"] == "reference.tif"


def test_classical_only_benchmark_marks_ml_unavailable_and_counts_failures(prepared):
    def partial(image_path, **kwargs):
        assert set(kwargs) == {"weights", "device", "imgsz", "confidence"}
        if image_path.name == "page1.tif":
            raise InferenceError("Unknown layout", partial_boxes={CLASS_NAMES[0]: prepared["boxes"][CLASS_NAMES[0]]})
        return prepared["perfect"](image_path)

    output = prepared["root"] / "out"
    report = benchmark_manifest(prepared["manifest"], output, templates=prepared["templates"], predictors={"classical": partial}, save_previews=False)
    classical = report["backends"]["classical"]
    assert classical["status"] == "evaluated"
    assert classical["summary"]["documents"] == 2
    assert classical["summary"]["all_regions_iou_ge_threshold"]["fraction"] == 0.5
    assert classical["summary"]["classes"][CLASS_NAMES[1]]["iou_mean"] == 0.5
    assert report["backends"]["yolo"]["status"] == "unavailable"
    assert report["backends"]["hybrid"]["status"] == "unavailable"
    assert report["validation_ranking"] == ["classical"]
    assert (output / "benchmark.json").exists()
    with (output / "benchmark.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 3
    assert rows[0]["all_three_pass_count"] == "1"
    assert rows[1]["all_three_pass_fraction"] == ""


def test_paired_cached_hybrid_preserves_failed_yolo_and_combines_runtime(prepared):
    calls = []

    def yolo(image_path, **_kwargs):
        if image_path.name == "page1.tif":
            raise InferenceError("Missing legend", partial_boxes={CLASS_NAMES[0]: prepared["boxes"][CLASS_NAMES[0]]})
        result = prepared["perfect"](image_path)
        result["bounding_boxes"][CLASS_NAMES[0]] = [11, 50, 21, 180]
        return result

    def refine(image_path, prediction, *, diagnostics=None):
        calls.append((image_path.name, copy.deepcopy(prediction["bounding_boxes"])))
        assert set(prediction) == {"image_name", "width", "height", "bounding_boxes"}
        assert prediction["bounding_boxes"][CLASS_NAMES[0]] == [11, 50, 21, 180]
        diagnostics.update(refined_regions=1)
        return prepared["perfect"](image_path)

    output = prepared["root"] / "out"
    with patch("bbox_detector.vision.refine_prediction", side_effect=refine):
        result = benchmark_manifest(prepared["manifest"], output, templates=prepared["templates"], weights="not-loaded.pt", predictors={"classical": prepared["perfect"], "yolo": yolo}, save_previews=False)
    assert len(calls) == 1
    assert result["paired_yolo_hybrid"]["document_mean_iou"] == {"improved": 1, "regressed": 0, "equal": 1}
    assert result["backends"]["hybrid"]["summary"]["documents"] == 2
    assert result["backends"]["hybrid"]["summary"]["classes"][CLASS_NAMES[2]]["missing_rate"] == 0.5
    hybrid = json.loads((output / "hybrid" / "report.json").read_text())
    for document in hybrid["documents"]:
        assert document["runtime_seconds"] == pytest.approx(document["refinement_runtime_seconds"] + document["yolo_runtime_seconds"])
    with (output / "hybrid" / "per_document.csv").open() as stream:
        assert float(next(csv.DictReader(stream))["runtime_seconds"]) == hybrid["documents"][0]["runtime_seconds"]


def test_setup_failure_is_reported_as_unavailable_without_hiding_classical(prepared):
    with patch("bbox_detector.inference.UltralyticsDetector", side_effect=RuntimeError("ML dependencies unavailable")):
        result = benchmark_manifest(prepared["manifest"], prepared["root"] / "out", templates=prepared["templates"], weights="best.pt", predictors={"classical": prepared["perfect"]}, save_previews=False)
    assert result["backends"]["classical"]["status"] == "evaluated"
    assert result["backends"]["yolo"]["status"] == "unavailable"
    assert "ML dependencies unavailable" in result["backends"]["yolo"]["reason"]
    assert result["backends"]["hybrid"]["status"] == "unavailable"


def test_native_iou_pairing_counts_regression_even_when_other_class_improves(prepared):
    truth = prepared["boxes"]
    before = copy.deepcopy(truth)
    after = copy.deepcopy(truth)
    before[CLASS_NAMES[0]] = [11, 50, 21, 180]
    after[CLASS_NAMES[1]] = [36, 50, 56, 180]
    original = {"split": "val", "documents": [{"image_name": "a.tif", "metrics": document_metrics(truth, before)}]}
    refined = {"split": "val", "documents": [{"image_name": "a.tif", "metrics": document_metrics(truth, after)}]}
    comparison = paired_comparison(original, refined)
    assert comparison["classes"][CLASS_NAMES[0]]["improved"] == 1
    assert comparison["classes"][CLASS_NAMES[1]]["regressed"] == 1
    assert comparison["document_mean_iou"]["improved"] == 1
    wrong = copy.deepcopy(refined)
    wrong["documents"][0]["image_name"] = "different.tif"
    with pytest.raises(ValueError, match="image sets"):
        paired_comparison(original, wrong)
    wrong = copy.deepcopy(refined)
    wrong["split"] = "test"
    with pytest.raises(ValueError, match="different splits"):
        paired_comparison(original, wrong)


def test_test_reports_do_not_select_backend(prepared):
    manifest = json.loads(prepared["manifest"].read_text())
    for record in manifest["records"]:
        if record["split"] == "val":
            record["split"] = "test"
    prepared["manifest"].write_text(json.dumps(manifest))
    report = benchmark_manifest(prepared["manifest"], prepared["root"] / "out", templates=prepared["templates"], split="test", predictors={"classical": prepared["perfect"]}, save_previews=False)
    assert report["validation_ranking"] == []


@pytest.mark.parametrize("backend,options", [("unknown", {}), ("yolo", {}), ("hybrid", {}), ("classical", {}), ("classical", {"templates": "bank", "weights": "best.pt"}), ("yolo", {"weights": "best.pt", "templates": "bank"})])
def test_invalid_backend_arguments_fail_before_detector_import(backend, options):
    with pytest.raises(ValueError):
        make_predictor(backend, **options)


def test_evaluation_cli_preserves_precomputed_predictions(prepared):
    import evaluate_bboxes

    predictions = prepared["root"] / "predictions"
    predictions.mkdir()
    for index in range(2):
        (predictions / f"page{index}.json").write_text(json.dumps(prepared["perfect"](prepared["root"] / f"page{index}.tif")))
    output = prepared["root"] / "out"
    assert evaluate_bboxes.main(["--manifest", str(prepared["manifest"]), "--predictions", str(predictions), "--output", str(output), "--no-previews"]) == 0
    report = json.loads((output / "report.json").read_text())
    assert report["prediction_source"] == "precomputed"
    assert report["summary"]["all_regions_iou_ge_threshold"]["fraction"] == 1
    with pytest.raises(ValueError, match="Precomputed"):
        evaluate_backend(prepared["manifest"], output, predictions_dir=predictions, templates=prepared["templates"])


def test_no_benchmark_sources_fails_explicitly(prepared):
    with pytest.raises(ValueError, match="template bank or trained weights"):
        benchmark_manifest(prepared["manifest"], prepared["root"] / "out")
