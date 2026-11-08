import json

from bbox_detector.common import CLASS_NAMES, write_json
from bbox_detector.evaluation import evaluate_manifest
from tools.evaluate_legend import main


def test_cached_review_selects_reconstructed_legend_without_loading_model(tmp_path):
    truth = dict(zip(CLASS_NAMES, ([10, 50, 20, 180], [35, 50, 55, 180], [0, 5, 90, 35])))
    payload = {"image_name": "page.tif", "width": 100, "height": 200, "bounding_boxes": truth}
    annotation = tmp_path / "annotation.json"
    write_json(annotation, payload)
    # No image exists: scoring and cached reconstruction require only predicted
    # geometry. This also proves the review never invokes model/image inference.
    manifest = tmp_path / "manifest.json"
    write_json(manifest, {"records": [{
        "image_name": "page.tif", "image_path": str(tmp_path / "page.tif"),
        "annotation_path": str(annotation), "width": 100, "height": 200,
        "group": "held-out-family", "split": "val",
    }]})
    base = tmp_path / "base"
    write_json(base / "page.json", {**payload, "bounding_boxes": {
        **truth, "lithology_legend": [20, 5, 80, 35],
    }})
    candidates = tmp_path / "raw_input"
    write_json(candidates / "page.json", {**payload, "bounding_boxes": {
        **truth, "lithology_legend": [20, 5, 60, 35],
    }, "diagnostics": {"legend_specialist": {"groups": [
        {"best_box": [20, 5, 60, 35], "best_confidence": 0.8},
        {"best_box": [0, 5, 30, 35], "best_confidence": 0.6},
        {"best_box": [55, 5, 90, 35], "best_confidence": 0.5},
    ]}}})
    cache = tmp_path / "raw"
    evaluate_manifest(manifest, None, cache, predictions_dir=candidates,
                      imgsz=64, conf=0.05, save_previews=False)
    output = tmp_path / "review"
    assert main([
        "--manifest", str(manifest), "--base-predictions", str(base),
        "--specialist-report", str(cache / "report.json"),
        "--output", str(output), "--imgsz", "64",
    ]) == 0
    comparison = json.loads((output / "comparison.json").read_text())
    assert comparison["selection"]["strategy"] == "legend_fragments"
    assert comparison["baseline"]["all_regions_iou_ge_threshold"]["documents"] == 0
    assert comparison["fragments"]["all_regions_iou_ge_threshold"]["documents"] == 1
    prediction = json.loads((output / "fragments/predictions/page.json").read_text())
    assert prediction["bounding_boxes"] == truth
