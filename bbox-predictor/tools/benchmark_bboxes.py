#!/usr/bin/env python3
"""Compare template/OpenCV, YOLO, and geometric refinement on held-out pages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bbox_detector.benchmark import benchmark_manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--templates", help="Training-only template bank directory")
    parser.add_argument("--weights", help="Optional trained YOLO checkpoint; omit for classical only")
    parser.add_argument("--output", required=True)
    parser.add_argument("--split", choices=("train", "val", "test"), default="val")
    parser.add_argument("--device", default=None)
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--iou-threshold", type=float, default=0.8)
    parser.add_argument("--no-previews", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = benchmark_manifest(args.manifest, args.output, templates=args.templates, weights=args.weights, split=args.split, device=args.device, imgsz=args.imgsz, conf=args.conf, iou_threshold=args.iou_threshold, save_previews=not args.no_previews)
    except (OSError, ValueError, RuntimeError, ImportError, KeyError) as exc:
        parser.exit(2, f"Benchmark failed: {exc}\n")
    print(json.dumps({"backends": report["backends"], "validation_ranking": report["validation_ranking"], "paired_yolo_hybrid": report["paired_yolo_hybrid"]}, indent=2))
    return 0 if any(result["status"] == "evaluated" for result in report["backends"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
