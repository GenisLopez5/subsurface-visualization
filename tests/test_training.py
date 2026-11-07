from pathlib import Path
from types import SimpleNamespace

import pytest

from bbox_detector.common import CLASS_NAMES
from bbox_detector.training import train_model, training_options


def dataset(tmp_path):
    for split in ("train", "val"):
        folder = tmp_path / "images" / split
        folder.mkdir(parents=True)
        (folder / "tile.png").touch()
    import yaml
    path = tmp_path / "dataset.yaml"
    path.write_text(yaml.safe_dump({"path": str(tmp_path), "train": "images/train", "val": "images/val", "names": list(CLASS_NAMES)}))
    return path


def test_training_preserves_text_and_does_not_cache_huge_images(tmp_path):
    options = training_options("dataset.yaml", tmp_path)
    assert (options["imgsz"], options["epochs"], options["patience"], options["batch"]) == (1536, 60, 10, 4)
    assert all(options[key] == 0 for key in ("fliplr", "flipud", "mosaic", "degrees", "perspective"))
    assert options["cache"] is False


def test_cuda_oom_retries_with_smaller_batch_and_records_checkpoint(tmp_path):
    data = dataset(tmp_path)
    calls = []

    class FakeYOLO:
        def __init__(self, weights):
            assert weights == "yolo11s.pt"

        def train(self, **options):
            calls.append(options["batch"])
            if options["batch"] == 4:
                raise RuntimeError("CUDA out of memory")
            run = Path(options["project"]) / "detector2"
            (run / "weights").mkdir(parents=True)
            (run / "weights" / "best.pt").write_bytes(b"fake checkpoint")
            self.trainer = SimpleNamespace(save_dir=run)
            return SimpleNamespace(results_dict={"metrics/mAP50-95(B)": 0.7})

    report = train_model(data, tmp_path / "training", model_factory=FakeYOLO)
    assert calls == [4, 2]
    assert Path(report["best_weights"]).is_file()
    assert report["attempts"][0]["status"] == "failed"
    assert report["options"]["batch"] == 2
    assert report["crop_validation_metrics"]["metrics/mAP50-95(B)"] == 0.7
    import json
    attempt = json.loads((tmp_path / "training" / "training_attempt.json").read_text())
    assert attempt["attempts"][-1]["status"] == "completed"


def test_audit_only_manifest_cannot_train_using_stale_existing_images(tmp_path):
    import json
    data = dataset(tmp_path)
    (tmp_path / "manifest.json").write_text(json.dumps({"materialized": False, "records": []}))
    with pytest.raises(ValueError, match="only audited"):
        train_model(data, tmp_path / "training", model_factory=lambda _: pytest.fail("Must not load a model"))


def test_non_oom_training_error_is_not_retried(tmp_path):
    calls = []

    class FakeYOLO:
        def __init__(self, weights):
            calls.append(weights)

        def train(self, **options):
            raise RuntimeError("Dataset is corrupt")

    with pytest.raises(RuntimeError, match="corrupt"):
        train_model(dataset(tmp_path), tmp_path / "training", model_factory=FakeYOLO)
    assert len(calls) == 1
