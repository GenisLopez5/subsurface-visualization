"""Paired development benchmark for legend reconstruction policies."""
from __future__ import annotations

import copy
import functools
import json
import math
from pathlib import Path

from .common import CLASS_NAMES, open_scan, read_crop, write_json
from .evaluation import _draw_comparison, evaluate_manifest
from .inference import share_column_vertical_extent
from .legend_geometry import refine_legend_frame
from .legend_pipeline import LegendPolicy, apply_legend_policy


def legend_summary(report, formats=None):
    """Missing legends count as failures, including within each format."""
    def summarize(documents):
        values = [doc["metrics"]["regions"]["lithology_legend"] for doc in documents]
        n = len(values)
        return {
            "documents": n,
            "iou_mean": sum(item["iou"] for item in values)/n if n else None,
            "iou_ge_0_8": sum(item["iou"] >= .8 for item in values),
            "iou_ge_0_9": sum(item["iou"] >= .9 for item in values),
            "fraction_iou_ge_0_8": sum(item["iou"] >= .8 for item in values)/n if n else None,
            "fraction_iou_ge_0_9": sum(item["iou"] >= .9 for item in values)/n if n else None,
            "missing": sum(item["missing"] for item in values),
        }
    summary = summarize(report["documents"])
    summary["edge_absolute_error_px"] = report["summary"]["classes"]["lithology_legend"]["edge_absolute_error_px"]
    summary["formats"] = {}
    if formats:
        for field in ("layout", "frame"):
            labels = {formats.get(doc["image_name"], {}).get(field) or "unreviewed" for doc in report["documents"]}
            summary["formats"][field] = {
                label: summarize([doc for doc in report["documents"] if (formats.get(doc["image_name"], {}).get(field) or "unreviewed") == label])
                for label in sorted(labels)
            }
    return summary


def reconstruction_policies():
    # Baseline first and previous merged policy before new policies: ties preserve
    # the existing behaviour rather than promoting extra complexity.
    policies = {
        "baseline": LegendPolicy(strategy="baseline"),
        "specialist": LegendPolicy(strategy="specialist"),
        "fragments_previous": LegendPolicy(),
    }
    for confidence in (.15, .30, .50):
        for overlap in (.60, .70):
            policies[f"fragments_c{confidence:.2f}_y{overlap:.2f}"] = LegendPolicy(
                min_relative_confidence=confidence, min_vertical_iou=overlap)
    policies["frame"] = LegendPolicy(strategy="frame")
    for confidence in (.15, .30, .50):
        for overlap in (.60, .70):
            policies[f"combined_c{confidence:.2f}_y{overlap:.2f}"] = LegendPolicy(
                strategy="fragments_frame", min_relative_confidence=confidence, min_vertical_iou=overlap)
    return policies


def save_policy_regressions(manifest, output, reports):
    """Save bounded before/after legend panels for every regressing experiment."""
    from PIL import Image

    output = Path(output)
    records = {r["image_name"]: r for r in json.loads(Path(manifest).read_text())["records"]}
    previous = {d["image_name"]: d for d in reports["fragments_previous"]["documents"]}
    saved = {}
    for policy, report in reports.items():
        if policy in {"baseline", "specialist", "fragments_previous"}:
            continue
        for doc in report["documents"]:
            name = doc["image_name"]
            before = previous[name]["metrics"]["regions"]["lithology_legend"]["iou"]
            after = doc["metrics"]["regions"]["lithology_legend"]["iou"]
            if after >= before:
                continue
            stem = Path(name).stem
            truth = json.loads(Path(records[name]["annotation_path"]).read_text())["bounding_boxes"]["lithology_legend"]
            old = json.loads((output/"fragments_previous/predictions"/f"{stem}.json").read_text())["bounding_boxes"].get("lithology_legend")
            new = json.loads((output/policy/"predictions"/f"{stem}.json").read_text())["bounding_boxes"].get("lithology_legend")
            boxes = [b for b in (truth, old, new) if b is not None]
            region = (max(0, math.floor(min(b[0] for b in boxes))-16),
                      max(0, math.floor(min(b[1] for b in boxes))-16),
                      min(records[name]["width"], math.ceil(max(b[2] for b in boxes))+16),
                      min(records[name]["height"], math.ceil(max(b[3] for b in boxes))+16))
            scale = min(1, 1100/(region[2]-region[0]), 900/(region[3]-region[1]))
            size = (max(1, round((region[2]-region[0])*scale)), max(1, round((region[3]-region[1])*scale)))
            panels = []
            with open_scan(records[name]["image_path"]) as scan:
                with read_crop(scan, region, size=size) as crop:
                    for title, box in ((f"Previous fragments | IoU {before:.3f}", old),
                                       (f"{policy} | IoU {after:.3f}", new)):
                        panels.append(_draw_comparison(crop, {"lithology_legend":truth},
                                      {"lithology_legend":box} if box else {}, region=region,
                                      title=title, canvas_width=1100))
            canvas = Image.new("RGB", (1100, sum(panel.height for panel in panels)), "white")
            offset = 0
            for panel in panels:
                canvas.paste(panel, (0,offset))
                offset += panel.height
                panel.close()
            target = output/"regressions"/policy/f"{stem}.jpg"
            target.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(target, quality=93)
            canvas.close()
            saved.setdefault(policy, []).append(str(target.relative_to(output)))
    write_json(output/"regressions.json", saved)
    return saved


def benchmark_legend_policies(manifest, base_predictions, specialist_predictions, output,
                             *, formats_path=None, save_previews=True):
    """Evaluate 6 fragment settings, frame-only, and 6 combinations on paired pages.

    Candidate inference is cached. Geometry is memoized across identical seeds;
    the selected policy is replayed on original TIFFs without that cache to time
    refinement. That timing still excludes the original detector inference.
    """
    manifest, output = Path(manifest), Path(output)
    base_predictions, specialist_predictions = Path(base_predictions), Path(specialist_predictions)
    source_report = specialist_predictions.parent / "report.json"
    if source_report.is_file():
        settings = json.loads(source_report.read_text()).get("settings", {})
        if settings.get("confidence", .05) != .05 or settings.get("imgsz", 2048) != 2048:
            raise ValueError("The policy grid requires specialist candidates at confidence=.05, imgsz=2048")
    formats = {}
    if formats_path:
        formats = {row["image_name"]: row for row in json.loads(Path(formats_path).read_text())["documents"]}
    records = json.loads(manifest.read_text())["records"]
    val = [record for record in records if record["split"] == "val"]
    base, details = {}, {}
    for record in val:
        name = record["image_name"]
        payload = json.loads((base_predictions/f"{Path(name).stem}.json").read_text())
        raw = json.loads((specialist_predictions/f"{Path(name).stem}.json").read_text())
        for item in (payload, raw):
            if item.get("image_name") != name or item.get("width") != record["width"] or item.get("height") != record["height"]:
                raise ValueError(f"Cached prediction metadata differs for {name}")
        if share_column_vertical_extent(payload["bounding_boxes"]) is None:
            raise ValueError(f"Both base columns are required for {name}")
        candidate_details = raw.get("diagnostics", {}).get("legend_specialist")
        if not isinstance(candidate_details, dict) or not isinstance(candidate_details.get("groups"), list):
            raise ValueError(f"Missing specialist candidate diagnostics for {name}")
        base[name], details[name] = payload, candidate_details

    @functools.lru_cache(maxsize=128)
    def cached_frame(path, seed, column_top):
        diagnostics = {}
        box = refine_legend_frame(path, seed, column_top=column_top, diagnostics=diagnostics)
        return box, diagnostics

    def frame(path, seed, *, column_top, diagnostics):
        box, value = cached_frame(str(path), tuple(seed), column_top)
        diagnostics.update(copy.deepcopy(value))
        return list(box)

    policies, reports, summaries = reconstruction_policies(), {}, {}
    for name, policy in policies.items():
        def predict(image_path, _policy=policy, **_kwargs):
            key = Path(image_path).name
            diagnostics = {}
            prediction = apply_legend_policy(
                image_path, base[key], _policy, candidate_details=details[key],
                frame_refiner=frame, diagnostics=diagnostics)
            for column in CLASS_NAMES[:2]:
                assert prediction["bounding_boxes"][column] == base[key]["bounding_boxes"][column]
            return {**prediction, "diagnostics": diagnostics}
        report = evaluate_manifest(manifest, None, output/name, predictor=predict, save_previews=False)
        reports[name] = report
        summaries[name] = legend_summary(report, formats)
        print(f"{name}: IoU={summaries[name]['iou_mean']:.4f}, >=0.8={summaries[name]['iou_ge_0_8']}/{len(val)}", flush=True)
    score = lambda name: (summaries[name]["iou_ge_0_8"], summaries[name]["iou_mean"])
    best = max(reports, key=score)
    policy = policies[best]
    config = {"version": 1, "policy": policy.to_dict(), "selected_strategy": best,
              "selection_metric": ["legend_documents_iou_ge_0_8", "legend_iou_mean"],
              "note": "Development-set selection; not an independent test result."}
    write_json(output/"selected_policy.json", config)

    def selected_predictor(image_path, **_kwargs):
        key = Path(image_path).name
        diagnostics = {}
        prediction = apply_legend_policy(image_path, base[key], policy,
                                         candidate_details=details[key], diagnostics=diagnostics)
        return {**prediction, "diagnostics": diagnostics}
    final = evaluate_manifest(manifest, None, output/"selected", predictor=selected_predictor,
                              save_previews=save_previews)
    previous = {doc["image_name"]: doc for doc in reports["fragments_previous"]["documents"]}
    regressions = [doc["image_name"] for doc in final["documents"]
                   if doc["metrics"]["regions"]["lithology_legend"]["iou"]
                   < previous[doc["image_name"]]["metrics"]["regions"]["lithology_legend"]["iou"]]
    comparison = {
        "version": 1, "selected_strategy": best, "policies": {name: policy.to_dict() for name, policy in policies.items()},
        "results": summaries, "selected": legend_summary(final, formats),
        "regressions_vs_previous": regressions,
        "runtime": final["summary"]["runtime_seconds"],
        "runtime_note": "Selected refinement on original images, without geometry cache; excludes cached YOLO inference and model loading.",
        "development_only": True,
    }
    if save_previews:
        comparison["regression_previews"] = save_policy_regressions(manifest, output, reports)
    write_json(output/"comparison.json", comparison)
    return comparison
