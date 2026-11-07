#!/usr/bin/env python3
"""Evaluate reconstructed document boxes, without a GPU for precomputed JSON."""

from __future__ import annotations

import argparse
import json

from bbox_detector.benchmark import evaluate_backend


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="Prepared document split manifest")
    parser.add_argument("--backend", choices=("yolo", "classical", "hybrid"), default="yolo")
    parser.add_argument("--templates", help="Training-only template bank directory, for classical")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--weights", help="Trained YOLO checkpoint")
    source.add_argument("--predictions", help="Directory of <image stem>.json prediction files")
    parser.add_argument("--output", required=True, help="Report, CSV, predictions, and comparison directory")
    parser.add_argument("--split", choices=("train", "val", "test"), default="val")
    parser.add_argument("--device", default=None, help="Ultralytics device, for example 0 or cpu")
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou-threshold", type=float, default=0.8)
    parser.add_argument("--no-previews", action="store_true", help="Skip bounded visual comparisons")
    args = parser.parse_args(argv)
    try:
        report = evaluate_backend(
            args.manifest,
            args.output,
            backend=args.backend,
            weights=args.weights,
            templates=args.templates,
            split=args.split,
            predictions_dir=args.predictions,
            device=args.device,
            imgsz=args.imgsz,
            conf=args.conf,
            iou_threshold=args.iou_threshold,
            save_previews=not args.no_previews,
        )
    except (OSError, ValueError, KeyError, RuntimeError, ImportError) as exc:
        parser.exit(2, f"Evaluation failed: {exc}\n")
    print(json.dumps(report["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
