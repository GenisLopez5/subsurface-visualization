"""Reconstruct document regions from overlapping, full-width strip detections.

Crop boundaries censor a column's vertical extent.  They are evidence of
continuity, not evidence of where the column starts or finishes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .common import CLASS_NAMES, Box


@dataclass(frozen=True)
class Detection:
    """A detection in original document coordinates with its source window."""

    class_id: int
    box: Box
    confidence: float
    tile: tuple[int, int, int, int]
    tile_id: int
    retry: bool = False


@dataclass(frozen=True)
class RegionEstimate:
    """A tentative region; missing boundaries must be resolved before export."""

    box: Box | None
    detections: tuple[Detection, ...] = ()
    missing_boundaries: tuple[str, ...] = ()
    top_detections: tuple[Detection, ...] = ()
    bottom_detections: tuple[Detection, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.box is not None and not self.missing_boundaries


def _median(values: Iterable[tuple[float, float]]) -> float:
    ordered = sorted(values)
    halfway = sum(weight for _, weight in ordered) / 2
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= halfway:
            return value
    raise ValueError("A weighted median needs at least one positive-weight value")


def _horizontal_agreement(a: Box, b: Box) -> bool:
    intersection = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    union = max(a[2], b[2]) - min(a[0], b[0])
    return union > 0 and intersection / union >= 0.45


def _representative(detections: list[Detection]) -> Box:
    return tuple(
        _median((item.box[coordinate], item.confidence) for item in detections)
        for coordinate in range(4)
    )


def _x_clusters(detections: list[Detection]) -> list[list[Detection]]:
    """Use a robust representative so nearby false positives cannot chain away."""
    clusters: list[list[Detection]] = []
    for detection in sorted(detections, key=lambda item: item.confidence, reverse=True):
        matches = [cluster for cluster in clusters if _horizontal_agreement(detection.box, _representative(cluster))]
        if matches:
            best = min(matches, key=lambda cluster: abs(_representative(cluster)[0] - detection.box[0]))
            best.append(detection)
        else:
            clusters.append([detection])
    return clusters


def _continuous_runs(detections: list[Detection], width: int) -> list[list[Detection]]:
    runs: list[list[Detection]] = []
    end = -float("inf")
    # Permit small prediction jitter, never a missing half-window or a large gap.
    tolerance = max(2.0, width * 0.03)
    for detection in sorted(detections, key=lambda item: item.box[1]):
        if not runs or detection.box[1] > end + tolerance:
            runs.append([detection])
            end = detection.box[3]
        else:
            runs[-1].append(detection)
            end = max(end, detection.box[3])
    return runs


def _interior(detection: Detection, side: str) -> bool:
    margin = max(2.0, (detection.tile[3] - detection.tile[1]) * 0.03)
    if side == "top":
        return detection.box[1] > detection.tile[1] + margin
    return detection.box[3] < detection.tile[3] - margin


def _page_edge_supported(detection: Detection, run: list[Detection], side: str, height: int) -> bool:
    coordinate = 1 if side == "top" else 3
    edge = 0 if side == "top" else height
    margin = max(2.0, (detection.tile[3] - detection.tile[1]) * 0.03)
    if detection.tile[coordinate] != edge or abs(detection.box[coordinate] - edge) > margin:
        return False
    if detection.tile[1] == 0 and detection.tile[3] == height:
        return True
    # Independent overlapping windows must sustain a purported page-edge column.
    return any(
        other.tile != detection.tile
        and min(other.box[3], detection.box[3]) > max(other.box[1], detection.box[1])
        for other in run
    )


def _column(detections: list[Detection], width: int, height: int) -> RegionEstimate:
    candidates = [run for cluster in _x_clusters(detections) for run in _continuous_runs(cluster, width)]
    if not candidates:
        return RegionEstimate(None, missing_boundaries=("region",))

    def score(run: list[Detection]) -> tuple[float, int, float]:
        return (
            max(item.box[3] for item in run) - min(item.box[1] for item in run),
            len({item.tile for item in run}),
            sum(item.confidence for item in run) / len(run),
        )

    run = max(candidates, key=score)
    start = min(item.box[1] for item in run)
    end = max(item.box[3] for item in run)
    # Look for uncensored estimates near each end, rather than using the minimum
    # and maximum of every detection (which amplifies individual false positives).
    tolerance = width * 0.25
    tops = [item for item in run if item.box[1] <= start + tolerance and (_interior(item, "top") or _page_edge_supported(item, run, "top", height))]
    bottoms = [item for item in run if item.box[3] >= end - tolerance and (_interior(item, "bottom") or _page_edge_supported(item, run, "bottom", height))]
    missing = tuple(side for side, values in (("top", tops), ("bottom", bottoms)) if not values)
    x1 = _median((item.box[0], item.confidence) for item in run)
    x2 = _median((item.box[2], item.confidence) for item in run)
    y1 = _median((item.box[1], item.confidence) for item in tops) if tops else start
    y2 = _median((item.box[3], item.confidence) for item in bottoms) if bottoms else end
    if y1 >= y2:
        # Different endpoint subsets can disagree on short, jitter-separated
        # fragments. Preserve the observed run for recentered retries; a
        # reversed or empty combination never counts as a resolved region.
        y1, y2 = start, end
        missing = ("top", "bottom")
    return RegionEstimate((x1, y1, x2, y2), tuple(run), missing, tuple(tops), tuple(bottoms))


def _iou(a: Box, b: Box) -> float:
    intersection = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0.0


def _legend(detections: list[Detection], height: int) -> RegionEstimate:
    if not detections:
        return RegionEstimate(None, missing_boundaries=("region",))
    complete = [
        item for item in detections
        if (_interior(item, "top") or item.tile[1] == 0)
        and (_interior(item, "bottom") or item.tile[3] == height)
    ]
    if not complete:
        best = max(detections, key=lambda item: item.confidence)
        return RegionEstimate(best.box, (best,), ("legend",))
    clusters: list[list[Detection]] = []
    for detection in sorted(complete, key=lambda item: item.confidence, reverse=True):
        matches = [cluster for cluster in clusters if _iou(detection.box, _representative(cluster)) >= 0.35]
        if matches:
            matches[0].append(detection)
        else:
            clusters.append([detection])
    best = max(clusters, key=lambda cluster: (len({item.tile for item in cluster}), sum(item.confidence for item in cluster) / len(cluster)))
    return RegionEstimate(_representative(best), tuple(best))


def aggregate_detections(detections: Iterable[Detection], width: int, height: int) -> dict[str, RegionEstimate]:
    """Estimate each class independently, allowing legitimate cross-class overlap."""
    detections = list(detections)
    result = {}
    for class_id, name in enumerate(CLASS_NAMES):
        items = [item for item in detections if item.class_id == class_id]
        result[name] = _legend(items, height) if name == "lithology_legend" else _column(items, width, height)
    return result
