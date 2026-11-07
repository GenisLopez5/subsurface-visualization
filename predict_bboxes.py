#!/usr/bin/env python3
"""Predict document regions without loading or requiring annotation files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bbox_detector.common import write_json
from bbox_detector.inference import predict_document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True, help="Input TIFF, PNG or JPEG document")
    parser.add_argument("--weights", type=Path, required=True, help="Trained three-class best.pt checkpoint")
    parser.add_argument("--output", type=Path, required=True, help="Output JSON in original document coordinates")
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--device", default=None, help="For example cpu or 0")
    args = parser.parse_args(argv)
    # Atomic output creation occurs only after all three regions resolve.  Reject
    # aliases of the image/checkpoint rather than overwriting a source artifact.
    if args.output.resolve() in {args.image.resolve(), args.weights.resolve()}:
        print("Prediction failed: output must differ from image and weights", file=sys.stderr)
        return 2
    try:
        prediction = predict_document(args.image, weights=args.weights, imgsz=args.imgsz, confidence=args.conf, device=args.device)
        write_json(args.output, prediction)
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"Prediction failed: {exc}", file=sys.stderr)
        return 2
    print(f"Saved three regions to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
