"""Training wrapper for the one-class lithology legend specialist."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .legend_data import LEGEND_CLASS_NAMES
from .training import train_model


def train_legend_model(
    data: str | Path,
    output: str | Path,
    *,
    model: str | Path = "yolo11s.pt",
    epochs: int = 80,
    patience: int = 15,
    imgsz: int = 2048,
    batch: int = 2,
    device: str | None = None,
    workers: int = 2,
    seed: int = 42,
    max_hours: float | None = None,
    rect: bool = False,
    model_factory: Callable | None = None,
) -> dict:
    return train_model(
        data,
        output,
        model=model,
        class_names=LEGEND_CLASS_NAMES,
        run_name="legend_detector",
        epochs=epochs,
        patience=patience,
        imgsz=imgsz,
        batch=batch,
        device=device,
        workers=workers,
        seed=seed,
        max_hours=max_hours,
        rect=rect,
        model_factory=model_factory,
    )
