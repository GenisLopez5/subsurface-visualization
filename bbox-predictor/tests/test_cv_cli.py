"""The alternative backends preserve the original CLI and JSON contract."""
import json
from types import SimpleNamespace
import sys

import pytest

import predict_bboxes
from bbox_detector.common import CLASS_NAMES
from bbox_detector.inference import InferenceError


def payload():
    return {"image_name": "scan.tif", "width": 100, "height": 300,
            "bounding_boxes": dict(zip(CLASS_NAMES, [[10, 100, 20, 280], [40, 100, 60, 280], [10, 10, 90, 50]]))}


def test_classical_cli_requires_no_weights_and_saves_diagnostics(tmp_path, monkeypatch):
    calls = []

    class FakeClassical:
        def __init__(self, bank):
            calls.append(bank)

        def predict_document(self, image, *, diagnostics):
            diagnostics["matched_template"] = "reference"
            return payload()

    monkeypatch.setitem(sys.modules, "bbox_detector.classical", SimpleNamespace(ClassicalDetector=FakeClassical))
    monkeypatch.setattr(predict_bboxes, "predict_document", lambda *a, **k: pytest.fail("Classical must not call YOLO"))
    output, diagnostics = tmp_path / "out.json", tmp_path / "diagnostics.json"
    assert predict_bboxes.main(["--backend", "classical", "--templates", str(tmp_path), "--image", "scan.tif",
                               "--output", str(output), "--diagnostics", str(diagnostics)]) == 0
    assert json.loads(output.read_text()) == payload()
    assert json.loads(diagnostics.read_text())["matched_template"] == "reference"
    assert len(calls) == 1


def test_hybrid_calls_geometric_refinement_after_yolo(tmp_path, monkeypatch):
    base = payload()
    refined = payload()
    refined["bounding_boxes"][CLASS_NAMES[0]] = [11, 100, 21, 280]
    monkeypatch.setattr(predict_bboxes, "predict_document", lambda *a, **k: base)

    def refine(image, prediction, *, max_shift_fraction, max_shift_px, diagnostics):
        assert prediction == base
        assert max_shift_fraction == 0.15 and max_shift_px == 12
        diagnostics["changed"] = 1
        return refined

    monkeypatch.setitem(sys.modules, "bbox_detector.vision", SimpleNamespace(refine_prediction=refine))
    out = tmp_path / "hybrid.json"
    assert predict_bboxes.main(["--backend", "hybrid", "--image", "scan.tif", "--weights", "best.pt", "--output", str(out)]) == 0
    assert json.loads(out.read_text()) == refined


def test_classical_failure_writes_diagnostic_not_success(tmp_path, monkeypatch):
    class Unmatched:
        def __init__(self, bank):
            pass

        def predict_document(self, *args, **kwargs):
            raise InferenceError("Unsupported layout", diagnostics={"header_inliers": 0})

    monkeypatch.setitem(sys.modules, "bbox_detector.classical", SimpleNamespace(ClassicalDetector=Unmatched))
    output, diagnostic = tmp_path / "out.json", tmp_path / "error.json"
    assert predict_bboxes.main(["--backend", "classical", "--templates", str(tmp_path), "--image", "scan.tif",
                               "--output", str(output), "--diagnostics", str(diagnostic)]) == 2
    assert not output.exists()
    assert json.loads(diagnostic.read_text())["inference"]["header_inliers"] == 0


def test_classical_cli_rejects_accidental_neural_weights(tmp_path):
    with pytest.raises(SystemExit) as caught:
        predict_bboxes.main(["--backend", "classical", "--templates", str(tmp_path), "--image", "scan.tif", "--weights", "best.pt", "--output", str(tmp_path / "out.json")])
    assert caught.value.code == 2


def test_diagnostic_path_cannot_overwrite_image(tmp_path):
    image = tmp_path / "scan.tif"
    image.write_bytes(b"source")
    assert predict_bboxes.main(["--backend", "classical", "--templates", str(tmp_path), "--image", str(image),
                               "--output", str(tmp_path / "out.json"), "--diagnostics", str(image)]) == 2
    assert image.read_bytes() == b"source"
