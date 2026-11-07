#!/usr/bin/env python3
"""Predict document regions without loading or requiring annotation files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bbox_detector.common import open_scan, write_json
from bbox_detector.inference import InferenceError, predict_document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True, help="Input TIFF, PNG or JPEG document")
    parser.add_argument("--backend", choices=("yolo", "classical", "hybrid"), default="yolo")
    parser.add_argument("--weights", type=Path, help="Trained three-class checkpoint for yolo/hybrid")
    parser.add_argument("--base-prediction", type=Path, help="Reuse existing column JSON instead of running the general detector")
    parser.add_argument("--legend-weights", type=Path, help="Optional one-class legend checkpoint")
    parser.add_argument("--legend-config", type=Path, help="Selected version=1 legend policy JSON")
    parser.add_argument("--legend-band-weights", type=Path, help="Optional rectangular-band refiner checkpoint")
    parser.add_argument("--legend-band-imgsz", type=int, default=1536)
    parser.add_argument("--templates", type=Path, help="Portable bank directory for the classical detector")
    parser.add_argument("--output", type=Path, required=True, help="Output JSON in original document coordinates")
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--device", default=None, help="For example cpu or 0")
    parser.add_argument("--diagnostics", type=Path, help="Optional separate JSON explaining matches or refinements")
    parser.add_argument("--preview-dir", type=Path, help="Optional visual review with final boxes and per-chunk detector evidence")
    parser.add_argument("--max-shift-px", type=int, default=12, help="Hybrid maximum native-pixel edge adjustment")
    parser.add_argument("--max-shift-fraction", type=float, default=0.15, help="Hybrid maximum adjustment as a fraction of column width")
    args = parser.parse_args(argv)
    if args.backend in {"yolo", "hybrid"} and args.weights is None and args.base_prediction is None:
        parser.error(f"--weights is required for backend {args.backend}")
    if args.base_prediction is not None and args.backend != "yolo":
        parser.error("--base-prediction requires backend yolo")
    if args.legend_config is not None and args.legend_weights is None:
        from bbox_detector.legend_pipeline import LegendPolicy
        try:
            if LegendPolicy.load(args.legend_config).strategy != "baseline":
                parser.error("--legend-weights is required by this legend policy")
        except (OSError, ValueError, TypeError) as exc:
            parser.error(str(exc))
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
    for source in (args.base_prediction, args.legend_weights, args.legend_config, args.legend_band_weights):
        if source is not None:
            sources.add(source.resolve())
    if args.output.resolve() in sources:
        print("Prediction failed: output must differ from all input files", file=sys.stderr)
        return 2
    if args.diagnostics is not None and args.diagnostics.resolve() in sources | {args.output.resolve()}:
        print("Prediction failed: diagnostics must differ from input files and output", file=sys.stderr)
        return 2
    details = {"backend": args.backend}
    trace: dict = {}
    try:
        if args.base_prediction is not None:
            prediction = json.loads(args.base_prediction.read_text())
            if not isinstance(prediction, dict) or not isinstance(prediction.get("bounding_boxes"), dict):
                raise ValueError("Cached prediction needs a JSON object with bounding_boxes")
            with open_scan(args.image) as scan:
                if (prediction.get("image_name") != args.image.name or prediction.get("width") != scan.width
                        or prediction.get("height") != scan.height):
                    raise ValueError("Cached prediction metadata differs from input image")
        elif args.backend == "classical":
            from bbox_detector.classical import ClassicalDetector

            prediction = ClassicalDetector(args.templates).predict_document(args.image, diagnostics=details)
        else:
            try:
                prediction = predict_document(
                    args.image,
                    weights=args.weights,
                    imgsz=args.imgsz,
                    confidence=args.conf,
                    device=args.device,
                    diagnostics=details,
                    trace=trace if args.preview_dir is not None else None,
                )
            except InferenceError as exc:
                if not args.legend_weights or not all(name in exc.partial_boxes for name in ("depth_column", "interpreted_lithology_column")):
                    raise
                with open_scan(args.image) as scan:
                    prediction = {"image_name": args.image.name, "width": scan.width, "height": scan.height,
                                  "bounding_boxes": exc.partial_boxes}
            if args.backend == "hybrid":
                from bbox_detector.vision import refine_prediction

                refinement = {}
                prediction = refine_prediction(args.image, prediction, max_shift_fraction=args.max_shift_fraction,
                                               max_shift_px=args.max_shift_px, diagnostics=refinement)
                details["refinement"] = refinement
        if args.legend_weights or args.legend_config or args.legend_band_weights:
            from bbox_detector.legend_inference import UltralyticsLegendDetector
            from bbox_detector.legend_pipeline import LegendPolicy, apply_legend_policy

            policy = LegendPolicy.load(args.legend_config) if args.legend_config else LegendPolicy(strategy="frame")
            detector = UltralyticsLegendDetector(args.legend_weights) if policy.strategy != "baseline" and args.legend_weights else None
            band_detector = UltralyticsLegendDetector(args.legend_band_weights) if args.legend_band_weights else None
            legend_details = {}
            prediction = apply_legend_policy(
                args.image, prediction, policy, detector=detector, device=args.device,
                band_detector=band_detector, band_imgsz=args.legend_band_imgsz, diagnostics=legend_details)
            details["legend"] = legend_details
        if set(prediction.get("bounding_boxes", {})) != {"depth_column", "interpreted_lithology_column", "lithology_legend"}:
            raise InferenceError("Prediction did not resolve all three regions", partial_boxes=prediction.get("bounding_boxes", {}))
        if args.preview_dir is not None:
            from bbox_detector.evaluation import save_prediction_review

            save_prediction_review(
                args.image,
                prediction["bounding_boxes"],
                args.preview_dir,
                trace=trace or None,
            )
        write_json(args.output, prediction)
        if args.diagnostics is not None:
            write_json(args.diagnostics, details)
    except (RuntimeError, ValueError, OSError, ImportError) as exc:
        if args.preview_dir is not None and trace:
            try:
                from bbox_detector.evaluation import save_prediction_review

                save_prediction_review(
                    args.image,
                    trace.get("final_boxes", {}),
                    args.preview_dir,
                    trace=trace,
                )
            except (RuntimeError, ValueError, OSError, ImportError) as preview_error:
                details["preview_error"] = str(preview_error)
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
