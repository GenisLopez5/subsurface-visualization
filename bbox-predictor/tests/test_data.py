import json
from pathlib import Path

import pytest
from PIL import Image

from bbox_detector.common import CLASS_NAMES, DEFAULT_DATASET, clamp_box, document_tiles, load_annotation, open_scan, read_crop
from bbox_detector.data import document_group, prepare_dataset, select_tiles, split_groups, yolo_labels


def annotation(name="scan.tif", width=100, height=700):
    return {"image_name": name, "width": width, "height": height, "bounding_boxes": {
        "depth_column": [10, 150, 15, 650],
        "interpreted_lithology_column": [55, 150, 75, 650],
        "lithology_legend": [20, 20, 80, 80],
    }}


def test_document_tiles_cover_exact_edges_and_short_images():
    tiles = document_tiles(100, 263)
    assert tiles == [(0, 0, 100, 100), (0, 50, 100, 150), (0, 100, 100, 200), (0, 150, 100, 250), (0, 163, 100, 263)]
    assert document_tiles(100, 40) == [(0, 0, 100, 40)]
    assert document_tiles(100, 100) == [(0, 0, 100, 100)]
    assert clamp_box((-0.1, 2.2, 101.5, 20.1), 100, 30) == [0, 2, 100, 21]
    with pytest.raises(ValueError):
        document_tiles(100, 200, overlap=1)


def test_all_visible_fragments_are_labelled_with_exclusive_geometry():
    labels = yolo_labels(annotation()["bounding_boxes"], (0, 75, 100, 175))
    assert len(labels) == 3  # Even the five-pixel legend fragment is labelled.
    assert list(map(float, labels[0].split()[1:])) == [0.125, 0.875, 0.05, 0.25]
    assert list(map(float, labels[2].split()[1:])) == [0.5, 0.025, 0.6, 0.05]


def test_sampler_prioritizes_full_legend_boundaries_and_negatives():
    data = annotation(height=1700)
    selected = select_tiles(data, max_tiles=16)
    assert len(selected) == 16
    assert len(set(selected)) == 16
    assert any(tile[1] <= 20 and tile[3] >= 80 for tile in selected)
    assert any(tile[1] < 150 < tile[3] for tile in selected)
    assert any(tile[1] < 650 < tile[3] for tile in selected)
    assert any(tile[1] >= 650 for tile in selected)
    assert all(tile[2] == 100 and tile[3] - tile[1] == 100 for tile in selected)


def test_known_families_and_group_split_are_reproducible():
    names = ["60817411400100_one.tif", "60817411400100_two.tiff", "60817411400200_three.tif", "Rio_other.tif", "Rio_else.tif", "independent.tif"]
    records = [{"group": document_group(name)} for name in names]
    split = split_groups(records)
    assert split == split_groups(list(reversed(records)))
    assert records[0]["group"] == records[2]["group"]
    assert records[3]["group"] == records[4]["group"]
    assert set(split.values()) == {"train", "val"}


@pytest.mark.parametrize("mode", ["1", "P", "RGB"])
def test_tiff_crop_rgb_and_resize(tmp_path, mode):
    image = Image.new(mode, (100, 200))
    if mode == "P":
        image.putpalette([255, 0, 0] + [0, 0, 0] * 255)
    path = tmp_path / f"{mode}.tif"
    image.save(path)
    guard = Image.MAX_IMAGE_PIXELS
    with open_scan(path) as scan:
        assert scan.size == (100, 200)
        crop = read_crop(scan, (0, 50, 100, 150), size=(25, 25))
        assert crop.mode == "RGB" and crop.size == (25, 25)
        if mode == "P":
            assert crop.getpixel((0, 0)) == (255, 0, 0)
    assert Image.MAX_IMAGE_PIXELS == guard


def test_export_splits_before_sampling_and_records_unannotated_duplicate(tmp_path):
    dataset = tmp_path / "input"
    (dataset / "images").mkdir(parents=True)
    (dataset / "bbox_annotation").mkdir()
    names = ["Rio_one.tif", "Rio_two.tif", "independent.tif"]
    for name in names:
        Image.new("RGB", (100, 700), "white").save(dataset / "images" / name)
        (dataset / "bbox_annotation" / f"{Path(name).stem}.json").write_text(json.dumps(annotation(name)))
    original = dataset / "images" / "Rio_one.tif"
    (dataset / "images" / "Rio_one(1).tif").write_bytes(original.read_bytes())
    manifest = prepare_dataset(dataset, tmp_path / "out", imgsz=32, max_tiles=4)
    assert manifest["summary"]["documents"] == 3
    assert manifest["excluded"][0]["reason"] == "duplicate_of_annotated_image"
    rio = [record for record in manifest["records"] if record["image_name"].startswith("Rio_")]
    assert rio[0]["split"] == rio[1]["split"]
    assert len({record["split"] for record in manifest["records"]}) == 2
    for record in manifest["records"]:
        assert len(record["tiles"]) <= 4
        for tile in record["tiles"]:
            with Image.open(tile["image_path"]) as bitmap:
                assert bitmap.size == (32, 32)
            assert Path(tile["label_path"]).is_file()
    assert json.loads((tmp_path / "out" / "dataset.yaml").read_text())["names"] == list(CLASS_NAMES)
    # A smaller rerun leaves old PNGs harmless: YAML consumes explicit lists.
    rerun = prepare_dataset(dataset, tmp_path / "out", imgsz=32, max_tiles=1, seed=9)
    config = json.loads((tmp_path / "out" / "dataset.yaml").read_text())
    assert config["train"] == "train.txt" and config["val"] == "val.txt"
    for split in ("train", "val"):
        listed = (tmp_path / "out" / f"{split}.txt").read_text().splitlines()
        expected = [tile["image_path"] for record in rerun["records"] if record["split"] == split for tile in record["tiles"]]
        assert listed == expected
    assert sum(len((tmp_path / "out" / f"{split}.txt").read_text().splitlines()) for split in ("train", "val")) == 3


def test_load_annotation_rejects_outside_or_wrong_dimensions(tmp_path):
    data = annotation()
    data["bounding_boxes"]["depth_column"][2] = 101
    path = tmp_path / "annotation.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="outside"):
        load_annotation(path)


def test_load_annotation_rejects_columns_with_different_vertical_extents(tmp_path):
    data = annotation()
    data["bounding_boxes"]["interpreted_lithology_column"][1] += 1
    path = tmp_path / "annotation.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="share ymin/ymax"):
        load_annotation(path)


def test_truncated_tiff_pixels_fail_with_actionable_io_error(tmp_path):
    path = tmp_path / "truncated.tif"
    Image.new("RGB", (32, 64), "white").save(path)
    # Keep a readable header/IFD, but remove almost all of its declared pixels.
    path.write_bytes(path.read_bytes()[:200])
    with open_scan(path) as scan:
        assert scan.size == (32, 64)
        with pytest.raises(OSError):
            read_crop(scan, (0, 0, 32, 64))


@pytest.mark.skipif(not DEFAULT_DATASET.exists(), reason="Bundled hackathon data unavailable")
def test_real_metadata_audit_has_51_documents_and_no_family_leakage(tmp_path):
    manifest = prepare_dataset(None, tmp_path / "audit", materialize=False)
    assert manifest["summary"]["documents"] == 51
    assert manifest["summary"]["boxes"] == 153
    assert manifest["summary"]["split_documents"] == {"train": 41, "val": 10}
    assert manifest["excluded"] == [{
        "image_name": "60811406050000_608114060500_g31765 001 st00 bp00__master log_5640-28740_5 in_md(1).tif",
        "reason": "duplicate_of_annotated_image",
        "duplicate_of": "60811406050000_608114060500_g31765 001 st00 bp00__master log_5640-28740_5 in_md.tif",
    }, {"image_name": "Thumbs.db", "reason": "non_image_file", "duplicate_of": None}]
    splits = {}
    for record in manifest["records"]:
        assert splits.setdefault(record["group"], record["split"]) == record["split"]
        assert len(record["tiles"]) <= 16
        assert not any(Path(tile["image_path"]).exists() for tile in record["tiles"])
