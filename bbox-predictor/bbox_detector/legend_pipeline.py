"""Portable legend policies shared by benchmarking and annotation-free inference."""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import time

from .common import CLASS_NAMES, clamp_box, open_scan, read_crop
from .inference import InferenceError
from .legend_inference import merge_aligned_legend_fragments, predict_legend


@dataclass(frozen=True)
class LegendPolicy:
    strategy: str = "fragments"
    min_relative_confidence: float = .5
    min_vertical_iou: float = .7
    max_gap_fraction: float = .05
    imgsz: int = 2048
    confidence: float = .05

    def __post_init__(self):
        if self.strategy not in {"baseline", "specialist", "fragments", "frame", "fragments_frame"}:
            raise ValueError("Unknown legend policy strategy")
        if not all(math.isfinite(v) for v in (self.min_relative_confidence, self.min_vertical_iou, self.max_gap_fraction, self.confidence)):
            raise ValueError("Legend policy thresholds must be finite")
        if not (0 < self.min_relative_confidence <= 1 and 0 < self.min_vertical_iou <= 1
                and 0 <= self.max_gap_fraction < 1 and 0 < self.confidence <= 1):
            raise ValueError("Invalid legend policy thresholds")
        if isinstance(self.imgsz, bool) or not isinstance(self.imgsz, int) or self.imgsz < 32 or self.imgsz % 32:
            raise ValueError("Legend imgsz must be a positive multiple of 32")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def load(cls, path):
        value = json.loads(Path(path).read_text())
        if value.get("version") != 1 or not isinstance(value.get("policy"), dict):
            raise ValueError("Legend configuration requires version=1 and policy")
        try:
            return cls(**value["policy"])
        except TypeError as exc:
            raise ValueError(f"Invalid legend policy fields: {exc}") from exc


def _checked_seed(box, width, height):
    if (not isinstance(box, (list, tuple)) or len(box) != 4
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in box)
            or not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)):
        raise ValueError("Legend candidate must be a valid native-coordinate box")
    return list(box)


def refine_legend_band(image_path, seed, *, column_top, detector, imgsz=1536,
                       confidence=.05, device=None, diagnostics=None):
    """Retry on a full-width rectangular band; map predictions to original pixels."""
    from .legend_geometry import legend_band

    if isinstance(imgsz, bool) or not isinstance(imgsz, int) or imgsz < 32 or imgsz % 32:
        raise ValueError("Band imgsz must be a positive multiple of 32")
    candidates = []
    with open_scan(image_path) as scan:
        band = legend_band(scan.width, scan.height, seed, column_top)
        size = (imgsz, max(1, round((band[3]-band[1])*imgsz/scan.width)))
        with read_crop(scan, band, size=size) as crop:
            sx, sy = scan.width/crop.width, (band[3]-band[1])/crop.height
            for raw in detector.predict(crop, imgsz=imgsz, confidence=confidence, device=device):
                if raw.class_id != 0 or not math.isfinite(raw.confidence) or raw.confidence < confidence:
                    continue
                x1, y1, x2, y2 = _checked_seed(list(raw.box), crop.width, crop.height)
                # A censored vertical edge does not establish the complete legend.
                if y1 <= 1 or y2 >= crop.height-1:
                    continue
                # Remove floating arithmetic noise before outward rounding.
                native = clamp_box([round(value, 9) for value in
                                    (x1*sx, band[1]+y1*sy, x2*sx, band[1]+y2*sy)], scan.width, scan.height)
                if (native[1]+native[3])/2 < column_top:
                    candidates.append((raw.confidence, native))
    chosen = max(candidates, key=lambda value: value[0])[1] if candidates else list(seed)
    if diagnostics is not None:
        diagnostics.update(band=list(band), candidate_count=len(candidates), selected_box=chosen,
                           reason="band_detection" if candidates else "band_retry_preserved_seed")
    return chosen


def apply_legend_policy(image_path, base_prediction, policy: LegendPolicy, *,
                        detector=None, candidate_details=None, device=None,
                        band_detector=None, band_imgsz=1536, frame_refiner=None,
                        diagnostics=None):
    """Replace only the legend; neither column's coordinates may change here."""
    started = time.perf_counter()
    result = {key: copy.deepcopy(base_prediction[key]) for key in ("image_name", "width", "height", "bounding_boxes")}
    boxes = result.get("bounding_boxes", {})
    width, height = result["width"], result["height"]
    if any(name not in boxes for name in CLASS_NAMES[:2]):
        raise InferenceError("Both columns are required before legend refinement", partial_boxes=boxes)
    for name in CLASS_NAMES[:2]:
        _checked_seed(boxes[name], width, height)
    top = min(boxes[name][1] for name in CLASS_NAMES[:2])
    details = copy.deepcopy(candidate_details) if candidate_details is not None else {}
    seed = boxes.get("lithology_legend")
    if policy.strategy != "baseline":
        if candidate_details is None:
            if detector is None:
                raise ValueError("This legend policy requires specialist weights or candidate diagnostics")
            try:
                predict_legend(image_path, column_top=top, detector=detector, imgsz=policy.imgsz,
                               confidence=policy.confidence, device=device, diagnostics=details)
            except InferenceError:
                pass
        groups = details.get("groups", [])
        for group in groups:
            _checked_seed(group["best_box"], width, height)
            if not math.isfinite(group["best_confidence"]) or not 0 <= group["best_confidence"] <= 1:
                raise ValueError("Invalid legend candidate confidence")
        if groups:
            seed = list(groups[0]["best_box"])
            if policy.strategy in {"fragments", "fragments_frame"}:
                seed = merge_aligned_legend_fragments(
                    groups, width=width, height=height,
                    min_relative_confidence=policy.min_relative_confidence,
                    min_vertical_iou=policy.min_vertical_iou, max_gap_fraction=policy.max_gap_fraction,
                )
    geometry = {}
    if seed is None:
        raise InferenceError("Neither base detector nor specialist resolved the legend", partial_boxes=boxes,
                             diagnostics={"legend_specialist": details})
    _checked_seed(seed, width, height)
    if policy.strategy in {"frame", "fragments_frame"}:
        if frame_refiner is None:
            from .legend_geometry import refine_legend_frame
            frame_refiner = refine_legend_frame
        seed = frame_refiner(image_path, seed, column_top=top, diagnostics=geometry)
    band_details = {}
    if band_detector is not None:
        seed = refine_legend_band(image_path, seed, column_top=top, detector=band_detector,
                                  imgsz=band_imgsz, confidence=policy.confidence,
                                  device=device, diagnostics=band_details)
    seed = _checked_seed(seed, width, height)
    boxes["lithology_legend"] = clamp_box(seed, width, height)
    result["bounding_boxes"] = {name: boxes[name] for name in CLASS_NAMES}
    if diagnostics is not None:
        diagnostics.update(policy=policy.to_dict(), legend_specialist=details, frame=geometry,
                           band_refiner=band_details, elapsed_seconds=time.perf_counter()-started)
    return result
