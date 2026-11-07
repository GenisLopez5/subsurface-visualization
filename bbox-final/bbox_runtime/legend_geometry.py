"""Recover a legend frame around a semantic seed using bounded image crops."""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

from .common import clamp_box, open_scan, read_crop


def legend_band(width: int, height: int, seed, column_top: int) -> tuple[int, int, int, int]:
    """Full-width band expanded by one seed height, bounded above the columns."""
    if len(seed) != 4 or not all(math.isfinite(float(v)) for v in seed):
        raise ValueError("A legend seed needs four finite coordinates")
    if not (0 <= seed[0] < seed[2] <= width and 0 <= seed[1] < seed[3] <= height):
        raise ValueError("Legend seed must have positive area inside the image")
    if not 0 < column_top <= height:
        raise ValueError("column_top must lie inside the image")
    extent = seed[3] - seed[1]
    top = max(0, math.floor(seed[1] - extent))
    bottom = min(height, column_top, math.ceil(seed[3] + extent))
    if bottom <= top or (seed[1] + seed[3]) / 2 >= bottom:
        raise ValueError("Legend seed is not above the column start")
    return 0, top, width, bottom


def _iou(a, b):
    inter = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union else 0


def find_legend_frame(gray: np.ndarray, seed, *, diagnostics: dict | None = None):
    """Return a supported enclosing frame in crop pixels, or the unchanged seed.

    Contours supply proposals, but all four edges are checked independently.
    Rounded corners are allowed. Ambiguous competing rectangles are rejected.
    """
    if gray.ndim != 2 or gray.dtype != np.uint8:
        raise ValueError("Expected a two-dimensional uint8 grayscale crop")
    h, w = gray.shape
    sh, sw = seed[3] - seed[1], seed[2] - seed[0]
    cx, cy = (seed[0] + seed[2]) / 2, (seed[1] + seed[3]) / 2
    dark = (gray < 190).astype(np.uint8) * 255
    # Close broken scan rules without fusing separated text paragraphs.
    closed = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    horizontal = cv2.morphologyEx(closed, cv2.MORPH_OPEN, np.ones((1, max(8, round(w * .06))), np.uint8))
    vertical = cv2.morphologyEx(closed, cv2.MORPH_OPEN, np.ones((max(8, round(sh * .35)), 1), np.uint8))
    masks = (closed, cv2.bitwise_or(horizontal, vertical))
    proposals = []
    radius = max(2, round(w / 700))
    for mask in masks:
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            x, y, bw, bh = cv2.boundingRect(contour)
            box = [x, y, x + bw, y + bh]
            if not (x <= cx < x + bw and y <= cy < y + bh):
                continue
            if bw < sw * .8 or not .6 * sh <= bh <= 2 * sh:
                continue
            overlap = max(0, min(box[3], seed[3]) - max(box[1], seed[1]))
            yiou = overlap / (max(box[3], seed[3]) - min(box[1], seed[1]))
            if yiou < .5:
                continue
            # Coverage uses a narrow edge strip, not filled area or text density.
            ink = dark > 0
            top = ink[max(0, y-radius):min(h, y+radius+1), x:x+bw].any(axis=0).mean()
            bottom = ink[max(0, y+bh-1-radius):min(h, y+bh+radius), x:x+bw].any(axis=0).mean()
            left = ink[y:y+bh, max(0, x-radius):min(w, x+radius+1)].any(axis=1).mean()
            right = ink[y:y+bh, max(0, x+bw-1-radius):min(w, x+bw+radius)].any(axis=1).mean()
            supports = [float(top), float(bottom), float(left), float(right)]
            if sum(v >= .65 for v in supports) < 3 or min(supports) < .35:
                continue
            score = .45 * float(np.mean(supports)) + .35 * yiou + .20 * min(1, bw / (2 * sw))
            if any(_iou(box, candidate["box"]) > .95 for candidate in proposals):
                continue
            proposals.append({"box": box, "score": score, "edge_support": supports})
    proposals.sort(key=lambda item: item["score"], reverse=True)
    reason = "no_supported_frame"
    chosen = list(seed)
    if proposals:
        best = proposals[0]
        ambiguous = any(best["score"] - other["score"] < .04 and _iou(best["box"], other["box"]) < .8
                        for other in proposals[1:])
        if ambiguous:
            reason = "ambiguous_frames"
        else:
            reason, chosen = "supported_frame", best["box"]
            # A table's heading may form a separate cell above its symbol body.
            # Recover its upper rule near the semantic seed's top rather than
            # returning the interior body rectangle alone.
            left, top, right, bottom = chosen
            first = max(0, math.floor(seed[1] - .25 * sh))
            last = min(top, math.ceil(seed[1] + .25 * sh))
            if last > first and right > left:
                profile = (horizontal[first:last+1, left:right] > 0).mean(axis=1)
                rows = np.flatnonzero(profile >= .85) + first
                if len(rows):
                    upper = int(min(rows, key=lambda y: abs(y-seed[1])))
                    sides = dark[upper:bottom, max(0, left-radius):min(w, left+radius+1)].any(axis=1).mean()
                    sides2 = dark[upper:bottom, max(0, right-1-radius):min(w, right+radius)].any(axis=1).mean()
                    if sides >= .65 and sides2 >= .65:
                        chosen = [left, upper, right, bottom]
    if diagnostics is not None:
        diagnostics.update(reason=reason, candidates=proposals[:12], selected_box=chosen)
    return chosen


def refine_legend_frame(image_path: str | Path, seed, *, column_top: int,
                        diagnostics: dict | None = None) -> list[int]:
    """Read only the full-width band and map a verified frame to native pixels."""
    details = {}
    with open_scan(image_path) as scan:
        band = legend_band(scan.width, scan.height, seed, column_top)
        scale = min(1.0, 2048 / scan.width)
        size = (max(1, round(scan.width * scale)), max(1, round((band[3]-band[1]) * scale)))
        with read_crop(scan, band, size=size) as crop:
            sx, sy = crop.width / scan.width, crop.height / (band[3]-band[1])
            local = [seed[0]*sx, (seed[1]-band[1])*sy, seed[2]*sx, (seed[3]-band[1])*sy]
            with crop.convert("L") as gray:
                result = find_legend_frame(np.asarray(gray), local, diagnostics=details)
            if details["reason"] == "supported_frame":
                native = clamp_box([result[0]/sx, band[1]+result[1]/sy, result[2]/sx,
                                    band[1]+result[3]/sy], scan.width, scan.height)
            else:
                native = list(seed)
    if diagnostics is not None:
        diagnostics.update(details, band=list(band), native_box=native)
    return native
