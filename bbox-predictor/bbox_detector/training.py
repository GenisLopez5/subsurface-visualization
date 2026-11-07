"""Reproducible YOLO11 fine-tuning, with small-batch recovery on CUDA OOM."""
from __future__ import annotations

import gc
import hashlib
import importlib.metadata
import json
import platform
import time
from pathlib import Path
from typing import Callable

from .common import CLASS_NAMES, write_json


def training_options(
    data: str | Path,
    output: str | Path,
    *,
    run_name: str = "detector",
    epochs: int = 60,
    patience: int = 10,
    imgsz: int = 1536,
    batch: int = 4,
    device: str | None = None,
    workers: int = 2,
    seed: int = 42,
    max_hours: float | None = None,
) -> dict:
    """Keep geometry and readable text intact while varying scale/illumination."""
    if not run_name or Path(run_name).name != run_name:
        raise ValueError("run_name must be a simple nonempty directory name")
    if epochs < 1 or patience < 0 or batch < 1 or workers < 0:
        raise ValueError("epochs/batch must be positive; patience/workers nonnegative")
    if imgsz < 32 or imgsz % 32:
        raise ValueError("imgsz must be a positive multiple of 32")
    if max_hours is not None and max_hours <= 0:
        raise ValueError("max_hours must be positive")
    options = {
        "data": str(Path(data).resolve()),
        "project": str(Path(output).resolve()),
        "name": run_name,
        "exist_ok": False,
        "epochs": epochs,
        "patience": patience,
        "imgsz": imgsz,
        "batch": batch,
        "workers": workers,
        "seed": seed,
        "deterministic": True,
        "pretrained": True,
        "optimizer": "AdamW",
        "lr0": 0.001,
        "cache": False,
        "amp": True,
        "plots": True,
        "save": True,
        "val": True,
        "mosaic": 0.0,
        "close_mosaic": 0,
        "mixup": 0.0,
        "copy_paste": 0.0,
        "fliplr": 0.0,
        "flipud": 0.0,
        "degrees": 0.0,
        "shear": 0.0,
        "perspective": 0.0,
        "translate": 0.03,
        "scale": 0.10,
        "hsv_h": 0.0,
        "hsv_s": 0.10,
        "hsv_v": 0.15,
    }
    if device is not None:
        options["device"] = device
    if max_hours is not None:
        options["time"] = max_hours
    return options


def validate_training_data(data: str | Path, class_names: tuple[str, ...] = CLASS_NAMES) -> dict:
    import yaml

    data = Path(data).resolve()
    with data.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, dict):
        raise ValueError("Dataset YAML must contain a mapping")
    names = config.get("names")
    if isinstance(names, dict):
        try:
            names = [names.get(i, names.get(str(i))) for i in range(len(names))]
        except (KeyError, TypeError) as error:
            raise ValueError("Invalid class mapping in dataset YAML") from error
    if names != list(class_names):
        raise ValueError(f"Dataset classes must be {list(class_names)!r} in that order")
    root = Path(config.get("path", data.parent))
    if not root.is_absolute():
        root = data.parent / root
    for split in ("train", "val"):
        value = config.get(split)
        if not isinstance(value, str):
            raise ValueError(f"Dataset YAML needs a {split} image directory or list file")
        path = Path(value)
        path = path if path.is_absolute() else root / path
        if not path.exists():
            raise ValueError(f"Missing {split} data: {path}; run prepare_dataset.py first")
        if path.is_dir() and not any(p.suffix.lower() in {".png", ".jpg", ".jpeg"} for p in path.iterdir()):
            raise ValueError(f"No images in {split}: {path}; an audit alone does not export tiles")
        if path.is_file() and path.suffix == ".txt":
            listed = [line.strip() for line in path.read_text().splitlines() if line.strip()]
            if not listed:
                raise ValueError(f"Empty {split} image list: {path}")
            for image_name in listed:
                image = Path(image_name)
                if not image.is_absolute():
                    image = path.parent / image
                if not image.is_file():
                    raise ValueError(f"Missing prepared image in {split}: {image}")
    manifest_path = data.parent / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if not manifest.get("materialized", True):
            raise ValueError("Dataset was only audited; run prepare_dataset.py without --audit-only")
        provenance = manifest.get("records", manifest.get("samples", []))
        groups = {split: {r["group"] for r in provenance if r["split"] == split} for split in ("train", "val")}
        if groups["train"] & groups["val"]:
            raise ValueError("Document-family leakage in the dataset manifest")
    return config


def _versions() -> dict:
    versions = {"python": platform.python_version()}
    for package in ("ultralytics", "torch", "torchvision", "numpy", "Pillow", "pyvips", "pyvips-binary", "PyYAML"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "not installed"
    return versions


def _is_cuda_oom(error: Exception) -> bool:
    message = str(error).lower()
    return "out of memory" in message and ("cuda" in message or "cudnn" in message)


def train_model(
    data: str | Path,
    output: str | Path = "artifacts/training",
    *,
    model: str | Path = "yolo11s.pt",
    class_names: tuple[str, ...] = CLASS_NAMES,
    run_name: str = "detector",
    epochs: int = 60,
    patience: int = 10,
    imgsz: int = 1536,
    batch: int = 4,
    device: str | None = None,
    workers: int = 2,
    seed: int = 42,
    max_hours: float | None = None,
    model_factory: Callable | None = None,
) -> dict:
    """Train once, retrying fresh at half batch only for CUDA memory failures.

    Native-document validation must subsequently be run with evaluate_bboxes.py;
    YOLO's best.pt is selected using validation on the sampled crops.
    """
    validate_training_data(data, class_names)
    if Path(model).suffix != ".pt":
        raise ValueError("Use pretrained .pt weights, for example yolo11s.pt")
    options = training_options(data, output, run_name=run_name, epochs=epochs, patience=patience, imgsz=imgsz,
                               batch=batch, device=device, workers=workers, seed=seed, max_hours=max_hours)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if model_factory is None:
        try:
            from ultralytics import YOLO
        except (ImportError, OSError, ValueError) as error:
            raise RuntimeError("Could not load Ultralytics/PyTorch. Install requirements.txt in a clean environment; see README.md for CPU setup.") from error
        model_factory = YOLO
    started = time.perf_counter()
    attempts = []
    while True:
        attempt = {"batch": options["batch"], "status": "running"}
        attempts.append(attempt)
        write_json(output / "training_attempt.json", {"model": str(model), "options": options,
                    "versions": _versions(), "attempts": attempts})
        detector = None
        retry = False
        try:
            detector = model_factory(str(model))
            results = detector.train(**options)
            attempt["status"] = "completed"
        except Exception as error:
            attempt.update(status="failed", error=str(error))
            write_json(output / "training_attempt.json", {"model": str(model), "options": options,
                        "versions": _versions(), "attempts": attempts})
            if not _is_cuda_oom(error) or options["batch"] <= 1:
                raise
            options["batch"] = max(1, options["batch"] // 2)
            retry = True
        if retry:
            # Drop the failed trainer/model before creating another CUDA model.
            del detector
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except (ImportError, OSError, ValueError):
                pass
            print(f"CUDA memory exhausted; restarting training with batch={options['batch']}", flush=True)
            continue
        break
    trainer = getattr(detector, "trainer", None)
    save_dir = Path(trainer.save_dir).resolve() if trainer is not None else output / run_name
    best = save_dir / "weights" / "best.pt"
    if not best.is_file():
        raise RuntimeError(f"Training returned without a best checkpoint: {best}")
    metrics = {}
    for name, value in getattr(results, "results_dict", {}).items():
        try:
            metrics[name] = float(value)
        except (TypeError, ValueError):
            continue
    summary = {
        "status": "completed", "model_initialization": str(model), "class_names": list(class_names), "best_weights": str(best),
        "best_weights_sha256": hashlib.sha256(best.read_bytes()).hexdigest(),
        "run_directory": str(save_dir), "data": str(Path(data).resolve()),
        "effective_batch_size": int(getattr(trainer, "batch_size", options["batch"])),
        "data_yaml_sha256": hashlib.sha256(Path(data).read_bytes()).hexdigest(),
        "options": options, "versions": _versions(), "attempts": attempts,
        "elapsed_seconds": time.perf_counter() - started, "crop_validation_metrics": metrics,
        "validation_note": "Crop metrics select the checkpoint; evaluate_bboxes.py measures the assembled document boxes.",
    }
    write_json(output / "training_attempt.json", {"model": str(model), "options": options,
                "versions": summary["versions"], "attempts": attempts})
    write_json(output / "training_summary.json", summary)
    return summary
