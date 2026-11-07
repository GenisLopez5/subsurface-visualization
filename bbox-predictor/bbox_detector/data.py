"""Document-level family splitting and YOLO crop preparation."""

from __future__ import annotations

import hashlib
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .common import CLASS_NAMES, document_tiles, find_dataset, load_annotation, open_scan, read_crop, write_json


def document_group(image_name: str) -> str:
    """Conservative, explicit family keys for known related hackathon logs."""
    name = image_name.lower()
    if "d-582" in name:
        return "family:d582"
    if any(identifier in name for identifier in ("60811405800000", "60811405800100", "60811405800300", "60811405800500")):
        return "family:mdn1"
    if any(identifier in name for identifier in ("60811406050000", "60811406050100")):
        return "family:ardennes"
    if any(identifier in name for identifier in ("60817410140000", "60817410140100")):
        return "family:mc822"
    if any(identifier in name for identifier in ("60817411400100", "60817411400200")):
        return "family:mc761"
    if "mc765_p011" in name:
        return "family:mc765_p011"
    if name.startswith("rio_"):
        return "family:rio"
    if name.startswith("critter creek"):
        return "family:critter_creek"
    return f"document:{Path(image_name).stem.lower()}"


def split_groups(records: list[dict[str, Any]], val_fraction: float = 0.2, seed: int = 42) -> dict[str, str]:
    """Choose a deterministic group subset nearest the desired document count."""
    if not 0 < val_fraction < 1:
        raise ValueError("val_fraction must be between 0 and 1")
    sizes = Counter(record["group"] for record in records)
    if len(sizes) < 2:
        raise ValueError("At least two independent document families are required for train/validation")
    groups = sorted(sizes)
    random.Random(seed).shuffle(groups)
    possibilities: dict[int, tuple[str, ...]] = {0: ()}
    for group in groups:
        for count, selected in list(possibilities.items()):
            possibilities.setdefault(count + sizes[group], (*selected, group))
    target = len(records) * val_fraction
    chosen = min((count for count in possibilities if 0 < count < len(records)), key=lambda count: (abs(count - target), count))
    val_groups = set(possibilities[chosen])
    return {group: "val" if group in val_groups else "train" for group in groups}


def intersection(box, tile) -> tuple[float, float, float, float] | None:
    x1, y1 = max(box[0], tile[0]), max(box[1], tile[1])
    x2, y2 = min(box[2], tile[2]), min(box[3], tile[3])
    return (x1, y1, x2, y2) if x2 > x1 and y2 > y1 else None


def yolo_labels(boxes: dict[str, list[float]], tile: tuple[int, int, int, int]) -> list[str]:
    """Clip every visible label, including short column/legend fragments."""
    width, height = tile[2] - tile[0], tile[3] - tile[1]
    lines = []
    for index, name in enumerate(CLASS_NAMES):
        clipped = intersection(boxes[name], tile)
        if clipped is None:
            continue
        x1, y1, x2, y2 = clipped
        values = ((x1 + x2 - 2 * tile[0]) / (2 * width), (y1 + y2 - 2 * tile[1]) / (2 * height), (x2 - x1) / width, (y2 - y1) / height)
        lines.append(f"{index} " + " ".join(f"{value:.9f}" for value in values))
    return lines


def select_tiles(annotation: dict[str, Any], max_tiles: int = 16) -> list[tuple[int, int, int, int]]:
    """Prioritize whole legends, column boundaries, bodies, and negatives."""
    if max_tiles <= 0:
        raise ValueError("max_tiles must be positive")
    width, height = annotation["width"], annotation["height"]
    grid = document_tiles(width, height)
    if height <= width:
        return grid
    boxes = annotation["bounding_boxes"]
    selected: list[tuple[int, int, int, int]] = []

    def at_center(y: float):
        top = max(0, min(height - width, round(y - width / 2)))
        return (0, top, width, top + width)

    def add(tile):
        if tile not in selected and len(selected) < max_tiles:
            selected.append(tile)

    legend = boxes["lithology_legend"]
    add(at_center((legend[1] + legend[3]) / 2))
    for name in CLASS_NAMES[:2]:
        box = boxes[name]
        add(at_center(box[1]))
        add(at_center(box[3]))
    # A negative remains genuinely empty; clipped legends are always labelled.
    negatives = [tile for tile in grid if not any(intersection(box, tile) for box in boxes.values())]
    if negatives:
        add(negatives[0])
        add(negatives[-1])
    for name in CLASS_NAMES[:2]:
        box = boxes[name]
        add(at_center((box[1] + box[3]) / 2))
    # Farthest-point filling spreads the remaining samples over the whole page.
    remaining = [tile for tile in grid if tile not in selected]
    while remaining and len(selected) < max_tiles:
        chosen = max(remaining, key=lambda tile: (min(abs(tile[1] - old[1]) for old in selected), -tile[1]))
        add(chosen)
        remaining.remove(chosen)
    return sorted(selected, key=lambda tile: tile[1])


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _scan_records(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    records, seen = [], set()
    for path in sorted((root / "bbox_annotation").glob("*.json")):
        annotation = load_annotation(path)
        image = root / "images" / annotation["image_name"]
        if not image.is_file():
            raise FileNotFoundError(f"Annotated image missing: {image}")
        if image.name in seen:
            raise ValueError(f"Duplicate image annotation: {image.name}")
        seen.add(image.name)
        with open_scan(image) as scan:
            if scan.size != (annotation["width"], annotation["height"]):
                raise ValueError(f"Header/annotation size mismatch for {image.name}: {scan.size}")
            records.append({
                "image_name": image.name, "image_path": str(image.resolve()), "annotation_path": str(path.resolve()),
                "width": scan.width, "height": scan.height, "mode": scan.mode,
                "group": document_group(image.name), "rows_per_strip": scan.rows_per_strip,
                "max_decoded_strip_bytes": scan.max_decoded_strip_bytes,
            })
    if not records:
        raise ValueError(f"No annotations found in {root / 'bbox_annotation'}")
    excluded = []
    for image in sorted((root / "images").iterdir()):
        if not image.is_file() or image.name in seen:
            continue
        if image.suffix.lower() not in (".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"):
            excluded.append({"image_name": image.name, "reason": "non_image_file", "duplicate_of": None})
            continue
        # Verify the supplied '(1)' duplicate rather than trusting its name.
        original = image.with_name(re.sub(r"\(\d+\)(?=\.[^.]+$)", "", image.name))
        duplicate = original != image and original.name in seen and original.stat().st_size == image.stat().st_size and _digest(original) == _digest(image)
        excluded.append({"image_name": image.name, "reason": "duplicate_of_annotated_image" if duplicate else "unannotated", "duplicate_of": original.name if duplicate else None})
    return records, excluded


def prepare_dataset(
    dataset: str | Path | None,
    output: str | Path,
    imgsz: int = 1536,
    max_tiles: int = 16,
    seed: int = 42,
    val_fraction: float = 0.2,
    materialize: bool = True,
) -> dict[str, Any]:
    """Audit, split original documents, then optionally export crop PNGs/labels."""
    if imgsz <= 0 or max_tiles <= 0:
        raise ValueError("imgsz and max_tiles must be positive")
    root, output = find_dataset(dataset), Path(output).expanduser().resolve()
    records, excluded = _scan_records(root)
    split = split_groups(records, val_fraction, seed)
    for record in records:
        record["split"] = split[record["group"]]
        annotation = load_annotation(record["annotation_path"])
        selected = select_tiles(annotation, max_tiles)
        stem = re.sub(r"[^a-zA-Z0-9_-]", "_", Path(record["image_name"]).stem)[:90]
        stem += "_" + hashlib.sha256(record["image_name"].encode()).hexdigest()[:8]
        record["tiles"] = []
        for index, box in enumerate(selected):
            basename = f"{stem}_{index:03d}"
            record["tiles"].append({
                "box": list(box),
                "image_path": str(output / "images" / record["split"] / f"{basename}.png"),
                "label_path": str(output / "labels" / record["split"] / f"{basename}.txt"),
            })
        if materialize:
            with open_scan(record["image_path"]) as scan:
                for tile in record["tiles"]:
                    box = tile["box"]
                    size = (imgsz, max(1, round(imgsz * (box[3] - box[1]) / (box[2] - box[0]))))
                    bitmap = read_crop(scan, box, size=size)
                    image_path, label_path = Path(tile["image_path"]), Path(tile["label_path"])
                    image_path.parent.mkdir(parents=True, exist_ok=True)
                    label_path.parent.mkdir(parents=True, exist_ok=True)
                    bitmap.save(image_path)
                    lines = yolo_labels(annotation["bounding_boxes"], box)
                    label_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    summary = {
        "documents": len(records), "boxes": len(records) * len(CLASS_NAMES),
        "families": len(split), "excluded": len(excluded),
        "split_documents": dict(Counter(record["split"] for record in records)),
        "split_tiles": dict(Counter(record["split"] for record in records for _ in record["tiles"])),
    }
    manifest = {
        "version": 1, "dataset_root": str(root), "output_root": str(output),
        "seed": seed, "val_fraction": val_fraction, "imgsz": imgsz, "max_tiles": max_tiles,
        "materialized": materialize, "class_names": list(CLASS_NAMES),
        "summary": summary, "records": records, "excluded": excluded,
    }
    write_json(output / "manifest.json", manifest)
    # Explicit lists prevent stale crops from a previous seed/crop budget from
    # entering either split when the same output directory is prepared again.
    for split_name in ("train", "val"):
        image_paths = [
            tile["image_path"]
            for record in records if record["split"] == split_name
            for tile in record["tiles"]
        ]
        (output / f"{split_name}.txt").write_text("\n".join(image_paths) + "\n", encoding="utf-8")
    # JSON is a YAML subset and avoids quoting errors in paths containing spaces.
    write_json(output / "dataset.yaml", {
        "path": str(output), "train": "train.txt", "val": "val.txt", "names": list(CLASS_NAMES),
    })
    return manifest
