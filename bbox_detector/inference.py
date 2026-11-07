"""Streaming document inference.  This module never reads annotations."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

from PIL import Image

from .aggregation import Detection, RegionEstimate, aggregate_detections
from .common import CLASS_NAMES, Box, clamp_box, document_tiles, open_scan, read_crop


@dataclass(frozen=True)
class RawDetection:
    """Box coordinates are pixels in the RGB image supplied to the detector."""

    class_id: int
    box: Box
    confidence: float = 1.0


class Detector(Protocol):
    def predict(self, image: Image.Image, *, imgsz: int, confidence: float, device: str | None) -> Iterable[RawDetection]: ...


class InferenceError(RuntimeError):
    def __init__(self, message: str, *, partial_boxes: dict[str, list[int]] | None = None, diagnostics: dict | None = None):
        super().__init__(message)
        self.partial_boxes = partial_boxes or {}
        self.diagnostics = diagnostics or {}


class UltralyticsDetector:
    """Lazy adapter, so preparation, geometry tests and evaluation need no torch."""

    def __init__(self, weights: str | Path):
        weights = Path(weights)
        if not weights.is_file():
            raise FileNotFoundError(f"Trained weights do not exist: {weights}")
        try:
            from ultralytics import YOLO
            self.model = YOLO(str(weights))
        except (ImportError, OSError, ValueError) as exc:
            raise RuntimeError(
                "Could not load Ultralytics/PyTorch. Install dependencies with "
                "pip install -r requirements.txt. For missing CUDA libraries, "
                "use the clean CPU environment described in README.md. "
                f"Original error: {exc}"
            ) from exc
        names = self.model.names
        actual = tuple(names.get(index) for index in range(len(names))) if isinstance(names, dict) else tuple(names)
        if actual != tuple(CLASS_NAMES):
            raise ValueError(f"Weights must contain class IDs {dict(enumerate(CLASS_NAMES))}; received {names!r}. Use trained best.pt, not the COCO pretrained checkpoint.")

    def predict(self, image: Image.Image, *, imgsz: int, confidence: float, device: str | None) -> Iterable[RawDetection]:
        options = {"source": image, "imgsz": imgsz, "conf": confidence, "verbose": False, "stream": True, "agnostic_nms": False}
        if device is not None:
            options["device"] = device
        for result in self.model.predict(**options):
            if result.boxes is None:
                continue
            for coordinates, class_id, score in zip(result.boxes.xyxy.cpu().tolist(), result.boxes.cls.cpu().tolist(), result.boxes.conf.cpu().tolist()):
                yield RawDetection(int(class_id), tuple(float(value) for value in coordinates), float(score))


def _retry_tiles(estimates: dict[str, RegionEstimate], width: int, height: int, seen: set[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    tiles = []
    maximum_start = max(0, height - width)
    for estimate in estimates.values():
        if estimate.resolved:
            continue
        if estimate.box is None:
            # A second grid offset by a quarter-window gives previously missed
            # regions a different context without inventing expected positions.
            centers = [min(height / 2, width * 0.75)]
        else:
            centers = []
            for boundary in estimate.missing_boundaries:
                if boundary == "top":
                    centers.append(estimate.box[1])
                elif boundary == "bottom":
                    centers.append(estimate.box[3])
                else:
                    centers.append((estimate.box[1] + estimate.box[3]) / 2)
        for center in centers:
            # With a 50% scan overlap the nominal centered crop can already be
            # on the first grid.  Offset it by a quarter-window in that case.
            for offset in (0, width * 0.25, -width * 0.25):
                top = max(0, min(maximum_start, int(round(center - width / 2 + offset))))
                tile = (0, top, width, min(height, top + width))
                if tile not in seen and tile not in tiles:
                    tiles.append(tile)
                    break
    return tiles


def predict_document(
    image_path: str | Path,
    weights: str | Path | None = None,
    *,
    detector: Detector | None = None,
    imgsz: int = 1536,
    confidence: float = 0.25,
    device: str | None = None,
    overlap: float = 0.5,
    diagnostics: dict | None = None,
) -> dict:
    """Return the team's three-region JSON schema, or raise InferenceError.

    ``detector`` is injectable for tests and local model integrations.  Its boxes
    refer to the supplied, resized RGB crop; inverse scaling happens here.
    ``diagnostics`` is an optional output mapping, kept outside the public JSON.
    """
    if isinstance(imgsz, bool) or not isinstance(imgsz, int) or imgsz < 32:
        raise ValueError("imgsz must be an integer of at least 32")
    if not math.isfinite(confidence) or not 0 < confidence <= 1:
        raise ValueError("confidence must be in (0, 1]")
    if not 0 <= overlap < 1:
        raise ValueError("overlap must be in [0, 1)")
    image_path = Path(image_path)
    if not image_path.is_file():
        raise FileNotFoundError(f"Document does not exist: {image_path}")
    if detector is None:
        if weights is None:
            raise ValueError("Provide trained weights or an injected detector")
        detector = UltralyticsDetector(weights)
    started = time.perf_counter()
    details = {"image_name": image_path.name, "scan_tiles": 0, "retry_tiles": 0}
    detections: list[Detection] = []
    with open_scan(image_path) as scan:
        width, height = scan.size
        tiles = document_tiles(width, height, overlap=overlap)

        def detect(tile: tuple[int, int, int, int], *, retry: bool = False) -> None:
            tile_id = details["scan_tiles"] + details["retry_tiles"]
            source_width, source_height = tile[2] - tile[0], tile[3] - tile[1]
            size = (imgsz, max(1, round(source_height * imgsz / source_width)))
            with read_crop(scan, tile, size=size) as crop:
                scale_x, scale_y = source_width / crop.width, source_height / crop.height
                for raw in detector.predict(crop, imgsz=imgsz, confidence=confidence, device=device):
                    if isinstance(raw.class_id, bool) or raw.class_id not in range(len(CLASS_NAMES)):
                        raise ValueError(f"Detector returned unknown class ID {raw.class_id!r}; expected 0, 1, 2")
                    if len(raw.box) != 4 or not all(math.isfinite(value) for value in raw.box):
                        raise ValueError("Detector returned non-finite or malformed box coordinates")
                    if not math.isfinite(raw.confidence) or not 0 <= raw.confidence <= 1:
                        raise ValueError("Detector returned confidence outside [0, 1]")
                    if raw.confidence < confidence:
                        continue
                    x1, y1, x2, y2 = raw.box
                    # Clipping small floating-point excursions is safe; zero-area
                    # and entirely off-crop predictions provide no evidence.
                    x1, x2 = max(0.0, x1), min(float(crop.width), x2)
                    y1, y2 = max(0.0, y1), min(float(crop.height), y2)
                    if x2 <= x1 or y2 <= y1:
                        continue
                    box = (tile[0] + x1 * scale_x, tile[1] + y1 * scale_y, tile[0] + x2 * scale_x, tile[1] + y2 * scale_y)
                    detections.append(Detection(int(raw.class_id), box, raw.confidence, tile, tile_id, retry))
            details["retry_tiles" if retry else "scan_tiles"] += 1

        for tile in tiles:
            detect(tile)
        estimates = aggregate_detections(detections, width, height)
        seen = set(tiles)
        # One recentered pass; repeating the same crop cannot resolve censorship.
        for tile in _retry_tiles(estimates, width, height, seen):
            if tile not in seen:
                detect(tile, retry=True)
                seen.add(tile)
        estimates = aggregate_detections(detections, width, height)

    boxes = {}
    unresolved = {}
    for name, estimate in estimates.items():
        if not estimate.resolved:
            unresolved[name] = list(estimate.missing_boundaries) or ["region"]
            continue
        box = estimate.box
        if (
            box is None
            or not all(math.isfinite(value) for value in box)
            or box[0] >= box[2]
            or box[1] >= box[3]
        ):
            unresolved[name] = ["invalid_box"]
            continue
        bounded = clamp_box(box, width, height)
        if bounded[0] >= bounded[2] or bounded[1] >= bounded[3]:
            unresolved[name] = ["invalid_box"]
        else:
            boxes[name] = bounded
    details.update({"width": width, "height": height, "detections": len(detections), "unresolved": unresolved, "elapsed_seconds": time.perf_counter() - started})
    if diagnostics is not None:
        diagnostics.update(details)
    if unresolved:
        summary = ", ".join(f"{name} ({'/'.join(boundaries)})" for name, boundaries in unresolved.items())
        raise InferenceError(f"Unresolved regions after recentered retry: {summary}", partial_boxes=boxes, diagnostics=details)
    return {"image_name": image_path.name, "width": width, "height": height, "bounding_boxes": boxes}
