"""Optional classical geometry helpers and conservative native-pixel refinement.

These routines propose geometry, not semantic class identities.  Refinement is
opt-in and must be compared with unrefined predictions on held-out documents.
No annotations or filename-specific templates are read here.
"""

from __future__ import annotations

import copy
import math
import time
from dataclasses import dataclass
from pathlib import Path

from .common import CLASS_NAMES, open_scan, read_crop


def _libraries():
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("Classical geometry needs OpenCV and NumPy; install requirements.txt") from exc
    return cv2, np


@dataclass(frozen=True)
class LineEvidence:
    """Local pixel position and half-open extent of a narrow straight rule."""

    position: float
    support: float
    thickness: int
    votes: int
    start: int
    end: int


def read_gray(scan, box, size=None):
    """Decode only the requested crop; optionally resize before RGB allocation."""
    _, np = _libraries()
    with read_crop(scan, box, size=size) as rgb:
        with rgb.convert("L") as gray:
            return np.asarray(gray, dtype=np.uint8).copy()


def vertical_lines(
    gray,
    *,
    min_length_fraction: float = 0.60,
    min_support: float = 0.65,
    max_thickness: int = 7,
) -> list[LineEvidence]:
    """Find narrow vertical rules across several dark/faint-line thresholds.

    Coordinates refer to ``gray``. Broad filled strips are rejected; a line's
    support measures vertical persistence, and votes count threshold agreement.
    """
    cv2, np = _libraries()
    gray = np.asarray(gray)
    if gray.ndim != 2 or gray.dtype != np.uint8:
        raise ValueError("gray must be a two-dimensional uint8 image")
    if not 0 < min_length_fraction <= 1 or not 0 < min_support <= 1 or max_thickness < 1:
        raise ValueError("Invalid line support, length or thickness")
    height, width = gray.shape
    if height < 4 or width < 1:
        return []
    length = max(3, round(height * min_length_fraction))
    gap = max(1, round(height * 0.015))
    masks = []
    for threshold in (96, 160, 208, 235, 247):
        masks.append(cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY_INV)[1])
    masks.append(cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1])
    masks.append(cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 5))
    found = []
    for vote_id, mask in enumerate(masks):
        if gap > 1:
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((gap, 1), dtype=np.uint8))
        opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((length, 1), dtype=np.uint8))
        profile = (opened > 0).mean(axis=0)
        indices = np.flatnonzero(profile >= min_support)
        if not len(indices):
            continue
        breaks = np.flatnonzero(np.diff(indices) > 1) + 1
        for run in np.split(indices, breaks):
            start, end = int(run[0]), int(run[-1]) + 1
            if end - start > max_thickness:
                continue
            found.append((start, end, float(profile[run].mean()), vote_id))
    clusters = []
    for evidence in sorted(found, key=lambda item: (item[0] + item[1]) / 2):
        center = (evidence[0] + evidence[1] - 1) / 2
        if clusters:
            previous = clusters[-1]
            previous_center = float(np.median([(item[0] + item[1] - 1) / 2 for item in previous]))
        if clusters and abs(center - previous_center) <= 1.5:
            clusters[-1].append(evidence)
        else:
            clusters.append([evidence])
    result = []
    for cluster in clusters:
        start = int(round(float(np.median([item[0] for item in cluster]))))
        end = int(round(float(np.median([item[1] for item in cluster]))))
        if end > start:
            result.append(LineEvidence((start + end - 1) / 2, max(item[2] for item in cluster), end - start, len({item[3] for item in cluster}), start, end))
    return result


def horizontal_lines(gray, **kwargs) -> list[LineEvidence]:
    """Find horizontal rules; positions are local row coordinates."""
    _, np = _libraries()
    return vertical_lines(np.asarray(gray).T.copy(), **kwargs)


def _edge_consensus(samples, original: int, shift_limit: float, side: str) -> dict:
    _, np = _libraries()
    candidates = []
    for sample_id, offset, lines in samples:
        for line in lines:
            coordinate = offset + (line.start if side == "left" else line.end)
            if line.votes >= 2 and abs(coordinate - original) <= shift_limit:
                candidates.append((coordinate, sample_id, line.support))
    groups = []
    for item in sorted(candidates):
        if groups and abs(item[0] - float(np.median([value[0] for value in groups[-1]]))) <= 1.5:
            groups[-1].append(item)
        else:
            groups.append([item])
    supported = [group for group in groups if len({item[1] for item in group}) >= 2]
    detail = {"original": original, "proposed": None, "accepted": False, "support_samples": 0, "candidates": [int(round(float(np.median([item[0] for item in group])))) for group in supported]}
    if not supported:
        detail["reason"] = "insufficient_line_support"
    elif len(supported) > 1:
        detail["reason"] = "ambiguous_parallel_rules"
    else:
        group = supported[0]
        proposed = int(round(float(np.median([item[0] for item in group]))))
        detail.update(proposed=proposed, accepted=True, support_samples=len({item[1] for item in group}), reason="consistent_rule" if proposed != original else "already_aligned")
    return detail


def _valid_native_box(box, width: int, height: int) -> bool:
    return (
        isinstance(box, (list, tuple))
        and len(box) == 4
        and all(not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and int(value) == value for value in box)
        and 0 <= box[0] < box[2] <= width
        and 0 <= box[1] < box[3] <= height
    )


def refine_prediction(
    image_path: str | Path,
    prediction: dict,
    *,
    max_shift_fraction: float = 0.15,
    max_shift_px: int = 12,
    diagnostics: dict | None = None,
) -> dict:
    """Optionally refine column X edges using three independent body strips.

    Both edges need unambiguous rules supported in at least two strips. Opposing
    inward shifts are rejected because a model box may intentionally include
    padding. Y extents and legends remain unchanged: fill transitions and nearby
    header rectangles do not establish their semantic boundaries.
    """
    if not math.isfinite(max_shift_fraction) or not 0 <= max_shift_fraction <= 0.5:
        raise ValueError("max_shift_fraction must be in [0, 0.5]")
    if isinstance(max_shift_px, bool) or not isinstance(max_shift_px, int) or max_shift_px < 0:
        raise ValueError("max_shift_px must be a nonnegative integer")
    if not isinstance(prediction, dict) or not isinstance(prediction.get("bounding_boxes"), dict):
        raise ValueError("prediction needs a bounding_boxes mapping")
    started = time.perf_counter()
    result = copy.deepcopy(prediction)
    details = {"backend": "opencv_geometry", "refined_regions": 0, "sample_crops": 0, "regions": {}}
    with open_scan(image_path) as scan:
        width, height = scan.size
        if prediction.get("width") != width or prediction.get("height") != height:
            raise ValueError("Prediction dimensions must match the original document")
        for name, original in prediction["bounding_boxes"].items():
            if name not in CLASS_NAMES:
                raise ValueError(f"Unknown region class: {name}")
            if not _valid_native_box(original, width, height):
                raise ValueError(f"Invalid native-coordinate box for {name}: {original!r}")
            box = [int(value) for value in original]
            record = {"original_box": box, "proposed_box": box, "accepted": False, "edges": {}}
            details["regions"][name] = record
            if name == "lithology_legend":
                record["reason"] = "legend_boundary_not_verified"
                continue
            limit = min(float(max_shift_px), (box[2] - box[0]) * max_shift_fraction)
            if limit < 1 or box[3] - box[1] < 64:
                record["reason"] = "insufficient_refinement_window"
                continue
            left = max(0, math.floor(box[0] - limit - 4))
            right = min(width, math.ceil(box[2] + limit + 4))
            strip_height = min(512, max(16, int((box[3] - box[1]) * 0.15)))
            samples = []
            seen = set()
            for fraction in (0.2, 0.5, 0.8):
                center = box[1] + fraction * (box[3] - box[1])
                top = max(box[1], min(box[3] - strip_height, round(center - strip_height / 2)))
                crop_box = (left, top, right, top + strip_height)
                if crop_box in seen:
                    continue
                seen.add(crop_box)
                gray = read_gray(scan, crop_box)
                samples.append((len(samples), left, vertical_lines(gray)))
                details["sample_crops"] += 1
            edge_left = _edge_consensus(samples, box[0], limit, "left")
            edge_right = _edge_consensus(samples, box[2], limit, "right")
            record["edges"] = {"left": edge_left, "right": edge_right, "top": {"accepted": False, "reason": "vertical_extent_preserved"}, "bottom": {"accepted": False, "reason": "vertical_extent_preserved"}}
            if not (edge_left["accepted"] and edge_right["accepted"]):
                record["reason"] = "both_edges_require_unambiguous_rules"
                for edge in (edge_left, edge_right):
                    edge["accepted"] = False
                continue
            proposed = [edge_left["proposed"], box[1], edge_right["proposed"], box[3]]
            record["proposed_box"] = proposed
            delta_left, delta_right = proposed[0] - box[0], proposed[2] - box[2]
            width_change = abs((proposed[2] - proposed[0]) - (box[2] - box[0]))
            if not _valid_native_box(proposed, width, height):
                reason = "invalid_proposed_geometry"
            elif delta_left > 1 and delta_right < -1:
                reason = "possible_intentional_padding"
            elif width_change > max(2, (box[2] - box[0]) * 0.10):
                reason = "inconsistent_column_width"
            else:
                reason = None
            if reason is not None:
                record["reason"] = reason
                edge_left["accepted"] = edge_right["accepted"] = False
                continue
            result["bounding_boxes"][name] = proposed
            changed = proposed != box
            record.update(accepted=changed, reason="refined_native_rules" if changed else "already_aligned")
            details["refined_regions"] += int(changed)
    details["elapsed_seconds"] = time.perf_counter() - started
    if diagnostics is not None:
        diagnostics.update(details)
    return result
