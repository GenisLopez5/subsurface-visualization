from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

from bbox_detector.inference import POSTPROCESS_VERSION
from tools.package_colab import ROOT, package_colab


def test_source_bundle_identifies_exact_inference_postprocess(tmp_path):
    target = package_colab(None, tmp_path / "source.zip", include_data=False)
    expected_hash = hashlib.sha256((ROOT / "bbox_detector" / "inference.py").read_bytes()).hexdigest()
    with zipfile.ZipFile(target) as archive:
        manifest = json.loads(archive.read("build_manifest.json"))
        bundled_hash = hashlib.sha256(archive.read("bbox_detector/inference.py")).hexdigest()
    assert manifest == {
        "postprocess_version": POSTPROCESS_VERSION,
        "inference_sha256": expected_hash,
        "inference_path": "bbox_detector/inference.py",
    }
    assert bundled_hash == expected_hash
