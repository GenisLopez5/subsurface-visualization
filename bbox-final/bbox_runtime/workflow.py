"""One image in, three final boxes out, with both packaged models enabled."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

from .common import CLASS_NAMES, open_scan
from .inference import InferenceError, UltralyticsDetector, predict_document
from .legend_inference import UltralyticsLegendDetector
from .legend_pipeline import LegendPolicy, apply_legend_policy

ROOT = Path(__file__).resolve().parent.parent


def model_digest(path):
    """sha256 of a .pt file, or of an exported model folder (every file, by name, in order)."""
    path = Path(path)
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode() + b"\0")
        digest.update(item.read_bytes())
    return digest.hexdigest()


class BoundingBoxPredictor:
    """Load models once and reuse them for documents; no labels or caches needed."""

    def __init__(self, *, device="auto", cpu_threads=2, models_dir=None, config_path=None):
        self.models_dir = Path(models_dir) if models_dir is not None else ROOT/"models"
        self.config_path = Path(config_path) if config_path is not None else ROOT/"pipeline.json"
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        if config.get("version") != 1:
            raise ValueError("Unsupported pipeline configuration version")
        self.policy = LegendPolicy.load(self.config_path)
        if self.policy.strategy not in {"frame", "fragments_frame"}:
            raise ValueError("The final workflow requires legend frame reconstruction")
        self.general_options = config["general"]
        if not isinstance(self.general_options, dict) or set(self.general_options) != {"imgsz", "confidence", "overlap"}:
            raise ValueError("General detector config requires imgsz, confidence and overlap")
        self.version = config["workflow_version"]
        manifest = json.loads((self.models_dir/"manifest.json").read_text(encoding="utf-8"))
        if manifest.get("version") != 1:
            raise ValueError("Unsupported model manifest version")
        self.model_paths, self.model_hashes = {}, {}
        for role, expected_classes in (("general", list(CLASS_NAMES)), ("legend", ["lithology_legend"])):
            metadata = manifest["models"][role]
            filename = metadata["file"]
            if not isinstance(filename, str) or Path(filename).name != filename:
                raise ValueError("Model filenames must be local to models/")
            path = self.models_dir/filename
            if not path.exists():
                raise FileNotFoundError(f"Missing packaged model: {path}")
            digest = model_digest(path)
            if digest != metadata["sha256"]:
                raise ValueError(f"Model checksum mismatch for {role}: {path}")
            if metadata["classes"] != expected_classes:
                raise ValueError(f"Incorrect class mapping for {role}")
            self.model_paths[role], self.model_hashes[role] = path, digest
        self.exported = any(path.is_dir() for path in self.model_paths.values())
        if device in (None, "auto"):
            if self.exported:
                device = "cpu"  # OpenVINO int8 export: runs on the CPU via OpenVINO
            else:
                import torch
                device = "0" if torch.cuda.is_available() else "cpu"
        self.device = str(device)
        if self.device == "cpu" and cpu_threads is not None:
            if isinstance(cpu_threads, bool) or not isinstance(cpu_threads, int) or cpu_threads < 1:
                raise ValueError("cpu_threads must be a positive integer or None")
            import torch
            torch.set_num_threads(cpu_threads)
        self.general = UltralyticsDetector(self.model_paths["general"])
        self.legend = UltralyticsLegendDetector(self.model_paths["legend"])

    def predict(self, image_path, *, diagnostics=None):
        """Return exclusive-maximum integer xyxy boxes in original-image pixels.

        Missing columns or an unresolved legend raise InferenceError. Partial
        results are never returned as successful three-region predictions.
        """
        image_path = Path(image_path).expanduser().resolve()
        if not image_path.is_file():
            raise FileNotFoundError(f"Image does not exist: {image_path}")
        started = time.perf_counter()
        general_details = {}
        try:
            base = predict_document(image_path, detector=self.general, device=self.device,
                                     diagnostics=general_details, **self.general_options)
        except InferenceError as error:
            if not all(name in error.partial_boxes for name in CLASS_NAMES[:2]):
                raise
            # A missed general-model legend is recoverable by the specialist.
            with open_scan(image_path) as scan:
                base = {"image_name": image_path.name, "width": scan.width, "height": scan.height,
                        "bounding_boxes": error.partial_boxes}
        original_columns = {name: list(base["bounding_boxes"][name]) for name in CLASS_NAMES[:2]}
        legend_details = {}
        result = apply_legend_policy(image_path, base, self.policy, detector=self.legend,
                                     device=self.device, diagnostics=legend_details)
        boxes = result.get("bounding_boxes", {})
        if set(boxes) != set(CLASS_NAMES):
            raise InferenceError("The final workflow did not resolve all three regions", partial_boxes=boxes)
        for name, box in boxes.items():
            if (not isinstance(box, list) or len(box) != 4
                    or any(isinstance(v, bool) or not isinstance(v, int) for v in box)
                    or not (0 <= box[0] < box[2] <= result["width"] and 0 <= box[1] < box[3] <= result["height"])):
                raise InferenceError(f"Invalid final box for {name}", partial_boxes=boxes)
        if any(boxes[name] != original_columns[name] for name in CLASS_NAMES[:2]):
            raise RuntimeError("Legend refinement changed a column box")
        if diagnostics is not None:
            diagnostics.update(workflow_version=self.version, device=self.device,
                               models_sha256=self.model_hashes, policy=self.policy.to_dict(),
                               general=general_details, legend=legend_details,
                               inference_seconds=time.perf_counter()-started)
        return result
