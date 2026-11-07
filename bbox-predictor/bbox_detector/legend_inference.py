"""Header-only inference and fusion for the lithology legend specialist."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Iterable, Protocol

from PIL import Image

from .common import CLASS_NAMES, clamp_box, open_scan, read_crop
from .inference import InferenceError, RawDetection, share_column_vertical_extent
from .legend_data import LEGEND_CLASS_NAMES


class LegendDetector(Protocol):
    def predict(
        self,
        image: Image.Image,
        *,
        imgsz: int,
        confidence: float,
        device: str | None,
    ) -> Iterable[RawDetection]: ...


class UltralyticsLegendDetector:
    def __init__(self, weights: str | Path):
        weights = Path(weights)
        if not weights.is_file():
            raise FileNotFoundError(f"Legend weights do not exist: {weights}")
        try:
            from ultralytics import YOLO
            self.model = YOLO(str(weights))
        except (ImportError, OSError, ValueError) as exc:
            raise RuntimeError(f"Could not load the legend specialist: {exc}") from exc
        names = self.model.names
        actual = tuple(names.get(index) for index in range(len(names))) if isinstance(names, dict) else tuple(names)
        if actual != LEGEND_CLASS_NAMES:
            raise ValueError(f"Legend weights need the single class {LEGEND_CLASS_NAMES}; received {names!r}")

    def predict(self, image: Image.Image, *, imgsz: int, confidence: float, device: str | None):
        options = {
            "source": image,
            "imgsz": imgsz,
            "conf": confidence,
            "verbose": False,
            "stream": True,
            "agnostic_nms": False,
        }
        if device is not None:
            options["device"] = device
        for result in self.model.predict(**options):
            if result.boxes is None:
                continue
            for coordinates, class_id, score in zip(
                result.boxes.xyxy.cpu().tolist(),
                result.boxes.cls.cpu().tolist(),
                result.boxes.conf.cpu().tolist(),
            ):
                yield RawDetection(int(class_id), tuple(float(value) for value in coordinates), float(score))


def _header_tiles(width: int, height: int, column_top: int, overlap: float) -> list[tuple[int, int, int, int]]:
    header_end = max(1, min(height, column_top))
    crop_height = min(width, height)
    if height <= crop_height or header_end <= crop_height:
        return [(0, 0, width, crop_height)]
    maximum_start = min(height - crop_height, header_end - crop_height)
    step = max(1, round(crop_height * (1 - overlap)))
    starts = list(range(0, maximum_start + 1, step))
    if starts[-1] != maximum_start:
        starts.append(maximum_start)
    return [(0, top, width, top + crop_height) for top in starts]


def _iou(first: list[float], second: list[float]) -> float:
    x1, y1 = max(first[0], second[0]), max(first[1], second[1])
    x2, y2 = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (first[2] - first[0]) * (first[3] - first[1]) + (second[2] - second[0]) * (second[3] - second[1]) - intersection
    return intersection / union if union > 0 else 0.0


def merge_aligned_legend_fragments(groups: list[dict], *, width: int, height: int) -> list[int]:
    """Join connected legend pieces in the selected candidate's horizontal band.

    Keep candidates with at least half the seed confidence, vertical interval
    IoU >= 0.7 with the seed, and horizontal gaps <= 5% of page width. The seed
    remains fixed so merging cannot drift into neighbouring rows. This optional
    reconstruction uses only detector evidence, never document annotations.
    """
    if not groups:
        raise ValueError("Cannot reconstruct a legend without candidate groups")
    seed = groups[0]["best_box"]
    merged = list(seed)
    remaining = list(groups[1:])
    while remaining:
        deferred = []
        added = False
        for group in remaining:
            box = group["best_box"]
            overlap = max(0, min(seed[3], box[3]) - max(seed[1], box[1]))
            extent = max(seed[3], box[3]) - min(seed[1], box[1])
            gap = max(0, merged[0] - box[2], box[0] - merged[2])
            if (group["best_confidence"] >= 0.5 * groups[0]["best_confidence"]
                    and extent > 0 and overlap / extent >= 0.7
                    and gap <= 0.05 * width):
                merged = [min(merged[0], box[0]), min(merged[1], box[1]),
                          max(merged[2], box[2]), max(merged[3], box[3])]
                added = True
            else:
                deferred.append(group)
        if not added:
            break
        remaining = deferred
    return clamp_box(merged, width, height)


def predict_legend(
    image_path: str | Path,
    *,
    column_top: int,
    weights: str | Path | None = None,
    detector: LegendDetector | None = None,
    imgsz: int = 2048,
    confidence: float = 0.10,
    device: str | None = None,
    overlap: float = 0.5,
    merge_fragments: bool = False,
    diagnostics: dict | None = None,
) -> list[int]:
    """Detect one complete legend by scanning only above the shared column top."""
    if isinstance(imgsz, bool) or not isinstance(imgsz, int) or imgsz < 32:
        raise ValueError("imgsz must be an integer of at least 32")
    if not math.isfinite(confidence) or not 0 < confidence <= 1:
        raise ValueError("confidence must be in (0, 1]")
    if not 0 <= overlap < 1:
        raise ValueError("overlap must be in [0, 1)")
    if detector is None:
        if weights is None:
            raise ValueError("Provide legend weights or an injected detector")
        detector = UltralyticsLegendDetector(weights)
    image_path = Path(image_path)
    candidates = []
    with open_scan(image_path) as scan:
        if not 0 < column_top <= scan.height:
            raise ValueError("column_top must lie inside the document")
        tiles = _header_tiles(scan.width, scan.height, column_top, overlap)
        for tile_id, tile in enumerate(tiles):
            source_width, source_height = tile[2] - tile[0], tile[3] - tile[1]
            target_height = max(1, round(source_height * imgsz / source_width))
            with read_crop(scan, tile, size=(imgsz, target_height)) as crop:
                scale_x, scale_y = source_width / crop.width, source_height / crop.height
                for raw in detector.predict(crop, imgsz=imgsz, confidence=confidence, device=device):
                    if raw.class_id != 0 or raw.confidence < confidence:
                        continue
                    if len(raw.box) != 4 or not all(math.isfinite(value) for value in raw.box):
                        raise ValueError("Legend detector returned a malformed box")
                    x1, y1, x2, y2 = raw.box
                    x1, x2 = max(0.0, x1), min(float(crop.width), x2)
                    y1, y2 = max(0.0, y1), min(float(crop.height), y2)
                    if x2 <= x1 or y2 <= y1:
                        continue
                    native = [
                        tile[0] + x1 * scale_x,
                        tile[1] + y1 * scale_y,
                        tile[0] + x2 * scale_x,
                        tile[1] + y2 * scale_y,
                    ]
                    if (native[1] + native[3]) / 2 >= column_top:
                        continue
                    candidates.append({"box": native, "confidence": float(raw.confidence), "tile_id": tile_id})
        width, height = scan.size
    groups = []
    for candidate in sorted(candidates, key=lambda value: value["confidence"], reverse=True):
        group = next((group for group in groups if _iou(candidate["box"], group[0]["box"]) >= 0.5), None)
        if group is None:
            groups.append([candidate])
        else:
            group.append(candidate)
    ranked = sorted(
        groups,
        key=lambda group: (max(item["confidence"] for item in group) + 0.05 * (len({item["tile_id"] for item in group}) - 1)),
        reverse=True,
    )
    details = {
        "header_tiles": len(_header_tiles(width, height, column_top, overlap)),
        "candidate_count": len(candidates),
        "groups": [
            {
                "support_tiles": len({item["tile_id"] for item in group}),
                "best_confidence": max(item["confidence"] for item in group),
                "best_box": clamp_box(group[0]["box"], width, height),
            }
            for group in ranked
        ],
    }
    if not ranked:
        details["reason"] = "legend_not_detected_in_header"
        if diagnostics is not None:
            diagnostics.update(details)
        raise InferenceError("Legend specialist found no candidate above the columns", diagnostics=details)
    selected = max(ranked[0], key=lambda value: value["confidence"])
    box = clamp_box(selected["box"], width, height)
    if merge_fragments:
        box = merge_aligned_legend_fragments(details["groups"], width=width, height=height)
    details.update(reason="selected_header_candidate", selected_box=box,
                   selection_policy="aligned_union" if merge_fragments else "best_candidate")
    if diagnostics is not None:
        diagnostics.update(details)
    return box


def fuse_legend_prediction(
    image_path: str | Path,
    base_prediction: dict,
    *,
    detector: LegendDetector,
    imgsz: int = 2048,
    confidence: float = 0.10,
    device: str | None = None,
    merge_fragments: bool = False,
    diagnostics: dict | None = None,
) -> dict:
    """Apply shared column Y, then replace only a successfully detected legend."""
    result = copy.deepcopy(base_prediction)
    boxes = result.get("bounding_boxes")
    if not isinstance(boxes, dict):
        raise ValueError("Base prediction needs bounding_boxes")
    shared = share_column_vertical_extent(boxes)
    if shared is None:
        raise InferenceError("Both column predictions are required before legend fusion", partial_boxes=boxes)
    specialist_details = {}
    try:
        legend = predict_legend(
            image_path,
            column_top=shared["top"],
            detector=detector,
            imgsz=imgsz,
            confidence=confidence,
            device=device,
            merge_fragments=merge_fragments,
            diagnostics=specialist_details,
        )
    except InferenceError:
        if "lithology_legend" not in boxes:
            raise InferenceError(
                "Neither base detector nor specialist resolved the legend",
                partial_boxes=boxes,
                diagnostics={"shared_column_vertical_extent": shared, "legend_specialist": specialist_details},
            )
    else:
        boxes["lithology_legend"] = legend
    if diagnostics is not None:
        diagnostics.update(shared_column_vertical_extent=shared, legend_specialist=specialist_details)
    result["bounding_boxes"] = {name: boxes[name] for name in CLASS_NAMES if name in boxes}
    return result


def make_cached_legend_fusion_predictor(
    predictions_dir: str | Path,
    legend_weights: str | Path,
    *,
    imgsz: int = 2048,
    confidence: float = 0.10,
    device: str | None = None,
    merge_fragments: bool = False,
):
    """Reuse existing column predictions while loading the legend model once."""
    root = Path(predictions_dir)
    detector = UltralyticsLegendDetector(legend_weights)

    def predict(image_path: str | Path, **_options):
        image_path = Path(image_path)
        with (root / f"{image_path.stem}.json").open(encoding="utf-8") as stream:
            base = json.load(stream)
        diagnostics = {}
        result = fuse_legend_prediction(
            image_path,
            base,
            detector=detector,
            imgsz=imgsz,
            confidence=confidence,
            device=device,
            merge_fragments=merge_fragments,
            diagnostics=diagnostics,
        )
        return {**result, "diagnostics": diagnostics}

    return predict
