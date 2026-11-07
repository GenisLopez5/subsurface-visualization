import copy
import json

from PIL import Image
import pytest

from bbox_detector.common import CLASS_NAMES
from bbox_detector.inference import InferenceError, RawDetection
from bbox_detector.legend_pipeline import LegendPolicy, apply_legend_policy, refine_legend_band


def base():
    return {"image_name": "scan.tif", "width": 100, "height": 400, "bounding_boxes": {
        CLASS_NAMES[0]: [5, 110, 10, 350], CLASS_NAMES[1]: [40, 120, 60, 370],
        CLASS_NAMES[2]: [20, 30, 80, 60],
    }, "evaluation_errors": ["Old cached error"]}


def test_policy_never_changes_columns_and_drops_stale_cache_errors():
    original = base()
    candidates = {"groups": [
        {"best_box": [30, 30, 60, 60], "best_confidence": .8},
        {"best_box": [0, 30, 40, 60], "best_confidence": .2},
    ]}
    details = copy.deepcopy(candidates)
    result = apply_legend_policy("unused.tif", original, LegendPolicy(min_relative_confidence=.15), candidate_details=candidates)
    assert result["bounding_boxes"][CLASS_NAMES[2]] == [0, 30, 60, 60]
    for name in CLASS_NAMES[:2]:
        assert result["bounding_boxes"][name] == original["bounding_boxes"][name]
    assert original == base() and candidates == details
    assert "evaluation_errors" not in result


def test_missing_specialist_and_missing_base_legend_is_explicit_error():
    original = base()
    del original["bounding_boxes"][CLASS_NAMES[2]]
    with pytest.raises(InferenceError, match="resolved") as caught:
        apply_legend_policy("unused.tif", original, LegendPolicy(), candidate_details={"groups": []})
    assert set(caught.value.partial_boxes) == set(CLASS_NAMES[:2])


def test_frame_without_support_keeps_seed(tmp_path):
    path = tmp_path / "scan.tif"
    Image.new("RGB", (100, 400), "white").save(path)
    result = apply_legend_policy(path, base(), LegendPolicy(strategy="frame"), candidate_details={"groups": []})
    assert result["bounding_boxes"] == base()["bounding_boxes"]


def test_band_refiner_preserves_aspect_and_maps_native_y(tmp_path):
    path = tmp_path / "scan.tif"
    Image.new("RGB", (100, 400), "white").save(path)
    class Detector:
        def predict(self, image, **kwargs):
            # Native band is y=0..90, resized by 0.64.
            assert image.size == (64, 58)
            yield RawDetection(0, (6.4, 58/3, 57.6, 58*2/3), .9)
    assert refine_legend_band(path, [20, 30, 80, 60], column_top=110,
                              detector=Detector(), imgsz=64) == [10, 30, 90, 60]


def test_censored_band_candidate_does_not_replace_seed(tmp_path):
    path = tmp_path / "scan.tif"
    Image.new("RGB", (100, 400), "white").save(path)
    class Detector:
        def predict(self, image, **kwargs):
            yield RawDetection(0, (0, 0, 64, image.height), .9)
    seed = [20, 30, 80, 60]
    assert refine_legend_band(path, seed, column_top=110, detector=Detector(), imgsz=64) == seed


def test_config_round_trip_and_unknown_fields(tmp_path):
    p = tmp_path / "config.json"
    policy = LegendPolicy(strategy="frame")
    p.write_text(json.dumps({"version": 1, "policy": policy.to_dict()}))
    assert LegendPolicy.load(p) == policy
    p.write_text(json.dumps({"version": 1, "policy": {"unexpected": True}}))
    with pytest.raises(ValueError, match="fields"):
        LegendPolicy.load(p)
