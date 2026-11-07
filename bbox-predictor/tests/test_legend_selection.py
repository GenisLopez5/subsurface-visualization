import copy

import pytest

from bbox_detector.legend_selection import select_legend_strategy


def report(mean, passing=0, names=("a.tif", "b.tif")):
    return {
        "summary": {
            "documents": len(names),
            "all_regions_iou_ge_threshold": {"threshold": 0.8, "documents": passing},
            "classes": {"lithology_legend": {"iou_mean": mean}},
        },
        "documents": [{"image_name": name} for name in names],
    }


def test_exported_specialist_regression_keeps_baseline():
    baseline = report(0.511888987)
    candidates = {conf: report(0.473773359) for conf in (0.05, 0.1, 0.15)}
    original = copy.deepcopy(candidates)
    selected = select_legend_strategy(baseline, candidates)
    assert selected["strategy"] == "baseline"
    assert selected["confidence"] is None
    assert selected["best_specialist_confidence"] == 0.05
    assert candidates == original


def test_more_complete_documents_is_primary_objective():
    selected = select_legend_strategy(report(0.7), {0.1: report(0.6, passing=1)})
    assert selected["strategy"] == "legend_specialist"
    assert selected["confidence"] == 0.1


def test_legend_mean_breaks_tie_on_complete_documents():
    selected = select_legend_strategy(report(0.5), {0.15: report(0.65), 0.1: report(0.7)})
    assert selected["confidence"] == 0.1


def test_exact_tie_and_no_candidates_keep_baseline():
    baseline = report(0.5)
    assert select_legend_strategy(baseline, {0.1: report(0.5)})["strategy"] == "baseline"
    assert select_legend_strategy(baseline, {})["strategy"] == "baseline"


def test_different_validation_documents_rejected():
    with pytest.raises(ValueError, match="same documents"):
        select_legend_strategy(report(0.5), {0.1: report(0.8, names=("a.tif", "c.tif"))})


def test_different_iou_threshold_rejected():
    candidate = report(0.8)
    candidate["summary"]["all_regions_iou_ge_threshold"]["threshold"] = 0.5
    with pytest.raises(ValueError, match="IoU threshold"):
        select_legend_strategy(report(0.5), {0.1: candidate})


@pytest.mark.parametrize("mean", [None, float("nan"), -0.1, 1.1])
def test_invalid_scores_rejected(mean):
    with pytest.raises(ValueError, match="Invalid validation score"):
        select_legend_strategy(report(0.5), {0.1: report(mean)})


def test_empty_validation_rejected():
    with pytest.raises(ValueError, match="nonempty"):
        select_legend_strategy(report(0.5, names=()), {})
