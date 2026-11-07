#!/usr/bin/env python3
"""Fine-tune YOLO11s on the prepared, family-separated crop dataset."""
from __future__ import annotations

import argparse
import json
import sys

from bbox_detector.training import train_model


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="artifacts/dataset/dataset.yaml")
    parser.add_argument("--output", default="artifacts/training")
    parser.add_argument("--model", default="yolo11s.pt")
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--patience", type=int, default=10)
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--device", help="0 for Colab GPU; cpu for local CPU")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-hours", type=float, help="Optional limit per training attempt (OOM retries restart)")
    args = parser.parse_args(argv)
    try:
        summary = train_model(**vars(args))
    except (ValueError, OSError, RuntimeError, ImportError) as error:
        print(f"Training failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"best_weights": summary["best_weights"], "elapsed_seconds": summary["elapsed_seconds"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
