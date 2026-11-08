#!/usr/bin/env python3
"""Compare cached base boxes, a legend specialist, and aligned-fragment fusion."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bbox_detector.common import write_json
from bbox_detector.evaluation import evaluate_manifest
from bbox_detector.inference import share_column_vertical_extent
from bbox_detector.legend_inference import (
    make_cached_legend_fusion_predictor, merge_aligned_legend_fragments,
)
from bbox_detector.legend_selection import select_legend_strategy


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--base-predictions", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--weights", type=Path)
    source.add_argument("--specialist-report", type=Path,
                        help="Reuse a previous evaluation's report.json and sibling predictions/")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--imgsz", type=int, default=2048)
    parser.add_argument("--conf", type=float, default=0.05)
    parser.add_argument("--save-previews", action="store_true")
    args = parser.parse_args(argv)

    def base_predictor(image_path, **_kwargs):
        payload = json.loads((args.base_predictions / f"{Path(image_path).stem}.json").read_text())
        if share_column_vertical_extent(payload["bounding_boxes"]) is None:
            raise ValueError("Both base columns are required for legend fusion")
        return payload

    baseline = evaluate_manifest(args.manifest, None, args.output / "baseline",
                                 predictor=base_predictor, save_previews=False)
    if args.weights:
        predictor = make_cached_legend_fusion_predictor(
            args.base_predictions, args.weights, imgsz=args.imgsz,
            confidence=args.conf, device=args.device,
        )
        raw = evaluate_manifest(args.manifest, None, args.output / "specialist",
                                predictor=predictor, imgsz=args.imgsz, conf=args.conf,
                                device=args.device, save_previews=False)
        cache = args.output / "specialist" / "predictions"
    else:
        raw = json.loads(args.specialist_report.read_text())
        cache = args.specialist_report.parent / "predictions"
        if not cache.is_dir():
            parser.error("The specialist report requires its sibling predictions/ directory")
        # Confirm the cached run was evaluated on this exact validation set.
        select_legend_strategy(baseline, {args.conf: raw})
        settings = raw.get("settings", {})
        if settings.get("confidence", args.conf) != args.conf or settings.get("imgsz", args.imgsz) != args.imgsz:
            parser.error("Cached specialist confidence/imgsz differs from the requested settings")

    def merged_predictor(image_path, **_kwargs):
        # Start from the specified base columns, avoiding stale columns in a cache.
        payload = copy.deepcopy(base_predictor(image_path))
        cached = json.loads((cache / f"{Path(image_path).stem}.json").read_text())
        if (cached.get("image_name") != payload["image_name"]
                or cached.get("width") != payload["width"] or cached.get("height") != payload["height"]):
            raise ValueError("Specialist cache does not match the base document")
        details = cached.get("diagnostics", {}).get("legend_specialist")
        if not isinstance(details, dict) or "groups" not in details:
            raise ValueError("Specialist predictions must include candidate-group diagnostics")
        groups = details["groups"]
        if groups:
            payload["bounding_boxes"]["lithology_legend"] = merge_aligned_legend_fragments(
                groups, width=payload["width"], height=payload["height"],
            )
        payload["diagnostics"] = {"legend_specialist": {
            **details, "selection_policy": "aligned_union",
            "selected_box": payload["bounding_boxes"].get("lithology_legend"),
        }}
        return payload

    merged = evaluate_manifest(args.manifest, None, args.output / "fragments",
                               predictor=merged_predictor, imgsz=args.imgsz, conf=args.conf,
                               save_previews=args.save_previews)
    raw_selection = select_legend_strategy(baseline, {args.conf: raw})
    merged_selection = select_legend_strategy(baseline, {args.conf: merged})
    selection = {**raw_selection}
    if merged_selection["selected_score"] > raw_selection["selected_score"]:
        selection = {**merged_selection, "strategy": "legend_fragments"}
    comparison = {
        "manifest": str(args.manifest.resolve()),
        "base_predictions": str(args.base_predictions.resolve()),
        "specialist_predictions": str(cache.resolve()),
        "baseline": baseline["summary"], "specialist": raw["summary"],
        "fragments": merged["summary"], "selection": selection,
        "note": "Strategy selected on validation; this is not an independent test result. "
                "Cached runtime excludes original detector inference.",
    }
    write_json(args.output / "comparison.json", comparison)
    print(json.dumps({name: {
        "legend_iou_mean": report["summary"]["classes"]["lithology_legend"]["iou_mean"],
        "complete_documents": report["summary"]["all_regions_iou_ge_threshold"]["documents"],
    } for name, report in (("baseline", baseline), ("specialist", raw), ("fragments", merged))}, indent=2))
    print("Selected strategy:", selection["strategy"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
