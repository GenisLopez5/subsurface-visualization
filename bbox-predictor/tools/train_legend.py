#!/usr/bin/env python3
"""Prepare header crops and train the one-class lithology legend specialist."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bbox_detector.legend_data import prepare_legend_dataset
from bbox_detector.legend_training import train_legend_model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="Family-separated source manifest")
    parser.add_argument("--dataset-output", default="artifacts/legend_dataset")
    parser.add_argument("--training-output", default="artifacts/legend_training")
    parser.add_argument("--model", default="yolo11s.pt")
    parser.add_argument("--imgsz", type=int, default=2048)
    parser.add_argument("--train-variants", type=int, default=4)
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--patience", type=int, default=15)
    parser.add_argument("--batch", type=int, default=2)
    parser.add_argument("--device", default=None)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    try:
        dataset = prepare_legend_dataset(
            args.manifest,
            args.dataset_output,
            imgsz=args.imgsz,
            train_variants=args.train_variants,
        )
        summary = train_legend_model(
            Path(args.dataset_output) / "dataset.yaml",
            args.training_output,
            model=args.model,
            epochs=args.epochs,
            patience=args.patience,
            imgsz=args.imgsz,
            batch=args.batch,
            device=args.device,
            workers=args.workers,
            seed=args.seed,
        )
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        parser.exit(2, f"Legend training failed: {exc}\n")
    print(json.dumps({
        "dataset_summary": dataset["summary"],
        "best_weights": summary["best_weights"],
        "elapsed_seconds": summary["elapsed_seconds"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
