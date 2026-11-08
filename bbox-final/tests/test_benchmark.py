import json
from pathlib import Path
from unittest.mock import patch
import zipfile

from PIL import Image

import benchmark


BOXES = {
    "depth_column": [5, 100, 15, 350],
    "interpreted_lithology_column": [40, 100, 55, 350],
    "lithology_legend": [0, 5, 90, 40],
}


def make_dataset(root: Path, names=("good.tif",)) -> Path:
    dataset = root / "test"
    (dataset / "images").mkdir(parents=True)
    (dataset / "annotations").mkdir()
    for name in names:
        image = dataset / "images" / name
        Image.new("RGB", (100, 400), "white").save(image)
        (dataset / "annotations" / f"{image.stem}.json").write_text(json.dumps({
            "image_name": name,
            "width": 100,
            "height": 400,
            "bounding_boxes": BOXES,
        }))
    return dataset


class Policy:
    def to_dict(self):
        return {"strategy": "fragments_frame"}


class FakePredictor:
    device = "cpu"
    version = "test-workflow"
    model_hashes = {"general": "general-hash", "legend": "legend-hash"}
    policy = Policy()
    events = []

    def __init__(self, **_kwargs):
        pass

    def predict(self, image, *, diagnostics):
        self.events.append(("predict", Path(image).name))
        if Path(image).stem == "bad":
            raise RuntimeError("controlled inference failure")
        diagnostics["fake"] = True
        return {
            "image_name": Path(image).name,
            "width": 100,
            "height": 400,
            "bounding_boxes": BOXES,
        }


def test_full_benchmark_scores_failures_and_reads_truth_after_prediction(tmp_path):
    dataset = make_dataset(tmp_path, ("good.tif", "bad.tif"))
    output = tmp_path / "report"
    events = []
    FakePredictor.events = events
    original_load_truth = benchmark._load_truth

    def load_truth(*args, **kwargs):
        events.append(("truth", args[1].name))
        return original_load_truth(*args, **kwargs)

    with patch("benchmark.BoundingBoxPredictor", FakePredictor), \
         patch("benchmark._select_models", return_value=(tmp_path, "cpu", "test")), \
         patch("benchmark._environment", return_value={"python": "test"}), \
         patch("benchmark._load_truth", side_effect=load_truth):
        assert benchmark.main([str(dataset), "--output", str(output)]) == 0

    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "finished"
    assert report["fresh_inference"] is True
    assert report["summary"]["documents"] == 2
    assert report["summary"]["all_regions_iou_ge_threshold"]["documents"] == 1
    assert report["summary"]["documents_with_inference_errors"] == 1
    assert report["summary"]["region_missing_rate"] == 0.5
    assert (output / "predictions" / "good.json").is_file()
    assert (output / "errors" / "bad.json").is_file()
    assert (output / "per_document.csv").is_file()
    assert (output / "index.html").is_file()
    assert events == [
        ("predict", "bad.tif"), ("truth", "bad.tif"),
        ("predict", "good.tif"), ("truth", "good.tif"),
    ]


def test_sponsor_style_zip_is_safely_discovered(tmp_path):
    dataset = make_dataset(tmp_path / "source")
    archive = tmp_path / "test.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        for path in dataset.rglob("*"):
            if path.is_file():
                handle.write(path, Path("test") / path.relative_to(dataset))
        handle.writestr("test/images/Thumbs.db", b"ignored")
    extracted = benchmark._extract_zip(archive, tmp_path / "extracted")
    root, annotations, images = benchmark._discover_directory(extracted)
    assert root == extracted.resolve()
    assert annotations.name == "annotations"
    assert [image.name for image in images] == ["good.tif"]


def test_rejects_unsafe_zip_member(tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../test/images/good.tif", b"not an image")
        handle.writestr("../test/annotations/good.json", b"{}")
    try:
        benchmark._extract_zip(archive, tmp_path / "extracted")
    except ValueError as error:
        assert "Unsafe ZIP member" in str(error)
    else:
        raise AssertionError("Unsafe archive member was accepted")
