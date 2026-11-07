"""Prepare a one-class, header-only dataset for lithology legend detection."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .common import load_annotation, open_scan, read_crop, write_json

LEGEND_CLASS_NAMES = ("lithology_legend",)


def _positive_tops(annotation: dict[str, Any], variants: int) -> list[int]:
    width, height = annotation["width"], annotation["height"]
    crop_height = min(width, height)
    legend = annotation["bounding_boxes"]["lithology_legend"]
    if legend[3] - legend[1] > crop_height:
        raise ValueError(f"Legend is taller than a full-width training crop: {annotation['image_name']}")
    minimum = max(0, math.ceil(legend[3] - crop_height))
    maximum = min(math.floor(legend[1]), height - crop_height)
    if minimum > maximum:
        raise ValueError(f"Cannot contain the complete legend in a header crop: {annotation['image_name']}")
    if variants == 1:
        return [round((minimum + maximum) / 2)]
    return [round(minimum + index * (maximum - minimum) / (variants - 1)) for index in range(variants)]


def _negative_top(annotation: dict[str, Any]) -> int | None:
    width, height = annotation["width"], annotation["height"]
    crop_height = min(width, height)
    if height <= crop_height:
        return None
    legend_bottom = annotation["bounding_boxes"]["lithology_legend"][3]
    column_top = min(annotation["bounding_boxes"][name][1] for name in ("depth_column", "interpreted_lithology_column"))
    top = min(height - crop_height, max(math.ceil(legend_bottom), math.floor(column_top)))
    return top if top > legend_bottom else None


def _label(legend: list[float], tile: tuple[int, int, int, int]) -> str:
    width, height = tile[2] - tile[0], tile[3] - tile[1]
    x1, y1, x2, y2 = legend
    if not (tile[0] <= x1 < x2 <= tile[2] and tile[1] <= y1 < y2 <= tile[3]):
        raise ValueError("Positive legend crops must contain the complete annotation")
    values = (
        (x1 + x2 - 2 * tile[0]) / (2 * width),
        (y1 + y2 - 2 * tile[1]) / (2 * height),
        (x2 - x1) / width,
        (y2 - y1) / height,
    )
    return "0 " + " ".join(f"{value:.9f}" for value in values)


def prepare_legend_dataset(
    manifest_path: str | Path,
    output_dir: str | Path,
    *,
    imgsz: int = 2048,
    train_variants: int = 4,
    include_negatives: bool = True,
) -> dict[str, Any]:
    """Export complete legends at several header positions without split leakage."""
    if imgsz < 32 or imgsz % 32:
        raise ValueError("imgsz must be a positive multiple of 32")
    if train_variants < 1:
        raise ValueError("train_variants must be positive")
    manifest_path = Path(manifest_path).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = manifest.get("records", [])
    if not records or any(record.get("split") not in {"train", "val"} for record in records):
        raise ValueError("The source manifest needs train/val document records")
    groups = {
        split: {record["group"] for record in records if record["split"] == split}
        for split in ("train", "val")
    }
    if not groups["train"] or not groups["val"] or groups["train"] & groups["val"]:
        raise ValueError("Legend preparation requires nonempty, family-disjoint train/val splits")

    samples = []
    for record in records:
        annotation = load_annotation(record["annotation_path"])
        if annotation["image_name"] != record["image_name"]:
            raise ValueError("Manifest image and annotation names disagree")
        split = record["split"]
        variants = train_variants if split == "train" else 1
        stem = re.sub(r"[^a-zA-Z0-9_-]", "_", Path(record["image_name"]).stem)[:80]
        stem += "_" + hashlib.sha256(record["image_name"].encode()).hexdigest()[:8]
        crop_height = min(annotation["width"], annotation["height"])
        definitions = [
            ("positive", top, index)
            for index, top in enumerate(_positive_tops(annotation, variants))
        ]
        negative_top = _negative_top(annotation) if include_negatives else None
        if negative_top is not None:
            definitions.append(("negative", negative_top, 0))
        with open_scan(record["image_path"]) as scan:
            if scan.size != (annotation["width"], annotation["height"]):
                raise ValueError(f"Image/annotation dimensions disagree: {record['image_name']}")
            for kind, top, index in definitions:
                tile = (0, top, scan.width, top + crop_height)
                suffix = f"{kind}_{index:02d}"
                image_path = output / "images" / split / f"{stem}_{suffix}.png"
                label_path = output / "labels" / split / f"{stem}_{suffix}.txt"
                image_path.parent.mkdir(parents=True, exist_ok=True)
                label_path.parent.mkdir(parents=True, exist_ok=True)
                target_height = max(1, round(crop_height * imgsz / scan.width))
                with read_crop(scan, tile, size=(imgsz, target_height)) as bitmap:
                    bitmap.save(image_path)
                line = _label(annotation["bounding_boxes"]["lithology_legend"], tile) if kind == "positive" else ""
                label_path.write_text(line + ("\n" if line else ""), encoding="utf-8")
                samples.append({
                    "image_name": record["image_name"],
                    "group": record["group"],
                    "split": split,
                    "kind": kind,
                    "tile": list(tile),
                    "image_path": str(image_path),
                    "label_path": str(label_path),
                })

    for split in ("train", "val"):
        paths = [sample["image_path"] for sample in samples if sample["split"] == split]
        (output / f"{split}.txt").write_text("\n".join(paths) + "\n", encoding="utf-8")
    write_json(output / "dataset.yaml", {
        "path": str(output),
        "train": "train.txt",
        "val": "val.txt",
        "names": list(LEGEND_CLASS_NAMES),
    })
    result = {
        "version": 1,
        "source_manifest": str(manifest_path),
        "imgsz": imgsz,
        "train_variants": train_variants,
        "include_negatives": include_negatives,
        "materialized": True,
        "class_names": list(LEGEND_CLASS_NAMES),
        "source_groups": {name: sorted(values) for name, values in groups.items()},
        "summary": {
            "documents": len(records),
            "samples": len(samples),
            "split_samples": dict(Counter(sample["split"] for sample in samples)),
            "kinds": dict(Counter(sample["kind"] for sample in samples)),
        },
        "samples": samples,
    }
    write_json(output / "manifest.json", result)
    return result
