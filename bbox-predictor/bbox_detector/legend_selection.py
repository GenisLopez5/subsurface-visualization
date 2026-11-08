"""Select a legend strategy using paired document validation, including baseline."""

from __future__ import annotations

import math
from collections.abc import Mapping


def select_legend_strategy(baseline: dict, candidates: Mapping[float, dict]) -> dict:
    """Promote a specialist only when its validation score strictly improves.

    Rank by complete documents at the requested IoU threshold, then by mean
    legend IoU (including missing boxes as zero). Exact ties keep the baseline.
    This selects a strategy on validation; it is not an independent test score.
    """
    def checked(report):
        summary = report["summary"]
        documents = report["documents"]
        names = [document["image_name"] for document in documents]
        count = summary["documents"]
        if not names or len(names) != len(set(names)) or len(names) != count:
            raise ValueError("Strategy selection requires nonempty, unique validation documents")
        passing = summary["all_regions_iou_ge_threshold"]
        threshold = passing["threshold"]
        mean = summary["classes"]["lithology_legend"]["iou_mean"]
        if (not isinstance(passing["documents"], int)
                or not 0 <= passing["documents"] <= count
                or not isinstance(mean, (int, float)) or not math.isfinite(mean)
                or not 0 <= mean <= 1
                or not isinstance(threshold, (int, float)) or not math.isfinite(threshold)
                or not 0 < threshold <= 1):
            raise ValueError("Invalid validation score for legend strategy selection")
        return set(names), threshold, (passing["documents"], mean)

    names, threshold, baseline_score = checked(baseline)
    selected_score = baseline_score
    selected_confidence = None
    best_specialist_confidence = None
    best_specialist_score = None
    # Sort thresholds so an exact specialist tie consistently picks the lowest.
    for confidence in sorted(candidates):
        if not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 < confidence <= 1:
            raise ValueError("Specialist confidence must be in (0, 1]")
        other_names, other_threshold, score = checked(candidates[confidence])
        if names != other_names or threshold != other_threshold:
            raise ValueError("Compare strategies on the same documents and IoU threshold")
        if best_specialist_score is None or score > best_specialist_score:
            best_specialist_score = score
            best_specialist_confidence = confidence
        if score > selected_score:
            selected_score = score
            selected_confidence = confidence

    return {
        "strategy": "baseline" if selected_confidence is None else "legend_specialist",
        "confidence": selected_confidence,
        "baseline_score": list(baseline_score),
        "selected_score": list(selected_score),
        "best_specialist_confidence": best_specialist_confidence,
        "best_specialist_score": list(best_specialist_score) if best_specialist_score is not None else None,
    }
