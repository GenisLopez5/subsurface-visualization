import json
from pathlib import Path

from PIL import Image

from bbox_detector.data import prepare_dataset
from bbox_detector.legend_data import LEGEND_CLASS_NAMES, prepare_legend_dataset


def _annotation(name):
    return {
        "image_name": name,
        "width": 100,
        "height": 700,
        "bounding_boxes": {
            "depth_column": [10, 150, 15, 650],
            "interpreted_lithology_column": [55, 150, 75, 650],
            "lithology_legend": [20, 20, 80, 80],
        },
    }


def test_legend_dataset_uses_train_variants_complete_boxes_and_negatives(tmp_path):
    dataset = tmp_path / "source"
    (dataset / "images").mkdir(parents=True)
    (dataset / "bbox_annotation").mkdir()
    for name in ("Rio_one.tif", "Rio_two.tif", "independent.tif"):
        Image.new("RGB", (100, 700), "white").save(dataset / "images" / name)
        (dataset / "bbox_annotation" / f"{Path(name).stem}.json").write_text(json.dumps(_annotation(name)))
    audit = prepare_dataset(dataset, tmp_path / "audit", materialize=False)
    result = prepare_legend_dataset(tmp_path / "audit" / "manifest.json", tmp_path / "legend", imgsz=64, train_variants=4)
    assert result["class_names"] == list(LEGEND_CLASS_NAMES)
    train_documents = sum(record["split"] == "train" for record in audit["records"])
    val_documents = sum(record["split"] == "val" for record in audit["records"])
    assert result["summary"]["split_samples"] == {
        "train": train_documents * 5,
        "val": val_documents * 2,
    }
    for sample in result["samples"]:
        with Image.open(sample["image_path"]) as image:
            assert image.size == (64, 64)
        label = Path(sample["label_path"]).read_text().strip()
        if sample["kind"] == "positive":
            values = label.split()
            assert values[0] == "0"
            assert all(0 < float(value) <= 1 for value in values[1:])
        else:
            assert label == ""
    config = json.loads((tmp_path / "legend" / "dataset.yaml").read_text())
    assert config["names"] == ["lithology_legend"]
