#!/usr/bin/env python3
"""Predict document regions without loading or requiring annotation files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bbox_detector.common import write_json
from bbox_detector.inference import InferenceError, predict_document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True, help="Input TIFF, PNG or JPEG document")
    parser.add_argument("--backend", choices=("yolo", "classical", "hybrid"), default="yolo")
    parser.add_argument("--weights", type=Path, help="Trained three-class checkpoint for yolo/hybrid")
    parser.add_argument("--templates", type=Path, help="Portable bank directory for the classical detector")
    parser.add_argument("--output", type=Path, required=True, help="Output JSON in original document coordinates")
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--device", default=None, help="For example cpu or 0")
    parser.add_argument("--diagnostics", type=Path, help="Optional separate JSON explaining matches or refinements")
    parser.add_argument("--max-shift-px", type=int, default=12, help="Hybrid maximum native-pixel edge adjustment")
    parser.add_argument("--max-shift-fraction", type=float, default=0.15, help="Hybrid maximum adjustment as a fraction of column width")
    args = parser.parse_args(argv)
    if args.backend in {"yolo", "hybrid"} and args.weights is None:
        parser.error(f"--weights is required for backend {args.backend}")
    if args.backend == "classical" and args.templates is None:
        parser.error("--templates is required for backend classical; build it with tools/build_templates.py")
    if args.backend == "classical" and args.weights is not None:
        parser.error("backend classical uses --templates, not --weights")
    # Atomic output creation occurs only after all three regions resolve.  Reject
    # aliases of the image/checkpoint rather than overwriting a source artifact.
    sources = {args.image.resolve()}
    if args.weights is not None:
        sources.add(args.weights.resolve())
    if args.templates is not None:
        sources.add((args.templates / "bank.json").resolve())
    if args.output.resolve() in sources:
        print("Prediction failed: output must differ from image, weights, and template bank", file=sys.stderr)
        return 2
    if args.diagnostics is not None and args.diagnostics.resolve() in sources | {args.output.resolve()}:
        print("Prediction failed: diagnostics must differ from input files and output", file=sys.stderr)
        return 2
    details = {"backend": args.backend}
    try:
        if args.backend == "classical":
            from bbox_detector.classical import ClassicalDetector

            prediction = ClassicalDetector(args.templates).predict_document(args.image, diagnostics=details)
        else:
            prediction = predict_document(args.image, weights=args.weights, imgsz=args.imgsz, confidence=args.conf, device=args.device, diagnostics=details)
            if args.backend == "hybrid":
                from bbox_detector.vision import refine_prediction

                refinement = {}
                prediction = refine_prediction(args.image, prediction, max_shift_fraction=args.max_shift_fraction,
                                               max_shift_px=args.max_shift_px, diagnostics=refinement)
                details["refinement"] = refinement
        write_json(args.output, prediction)
        if args.diagnostics is not None:
            write_json(args.diagnostics, details)
    except (RuntimeError, ValueError, OSError, ImportError) as exc:
        if args.diagnostics is not None:
            details.update(error=str(exc), status="error")
            if isinstance(exc, InferenceError):
                details.update(partial_boxes=exc.partial_boxes, inference=exc.diagnostics)
            try:
                write_json(args.diagnostics, details)
            except OSError as diagnostic_error:
                print(f"Could not save diagnostics: {diagnostic_error}", file=sys.stderr)
        print(f"Prediction failed: {exc}", file=sys.stderr)
        return 2
    print(f"Saved three regions to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
