"""Sequential rectangular-band training and final-document evaluation."""
from __future__ import annotations

import json
from pathlib import Path

from .common import write_json
from .evaluation import evaluate_manifest
from .inference import share_column_vertical_extent
from .legend_benchmark import legend_summary
from .legend_inference import UltralyticsLegendDetector
from .legend_pipeline import LegendPolicy, apply_legend_policy
from .legend_training import train_legend_model


def train_band_experiments(data, manifest, base_predictions, specialist_predictions,
                          policy_path, output, *, device="0", model="yolo11s.pt",
                          model_factory=None, detector_factory=None):
    """Change only imgsz across runs, evaluating each on inference-derived bands."""
    output = Path(output)
    base_predictions, specialist_predictions = Path(base_predictions), Path(specialist_predictions)
    policy = LegendPolicy.load(policy_path)
    records = json.loads(Path(manifest).read_text())["records"]
    bases, coarse = {}, {}
    for record in records:
        if record["split"] != "val":
            continue
        stem = Path(record["image_name"]).stem
        base = json.loads((base_predictions/f"{stem}.json").read_text())
        share_column_vertical_extent(base["bounding_boxes"])
        raw = json.loads((specialist_predictions/f"{stem}.json").read_text())
        bases[record["image_name"]] = base
        coarse[record["image_name"]] = raw["diagnostics"]["legend_specialist"]
    detector_factory = detector_factory or UltralyticsLegendDetector
    reports = {}

    def predictor(image_path, band_detector=None, band_imgsz=1536, **_kwargs):
        name = Path(image_path).name
        details = {}
        result = apply_legend_policy(image_path, bases[name], policy, candidate_details=coarse[name],
                                     band_detector=band_detector, band_imgsz=band_imgsz,
                                     device=device, diagnostics=details)
        return {**result, "diagnostics": details}

    reference = evaluate_manifest(manifest, None, output/"reference", predictor=predictor, save_previews=False)
    reports["reference"] = reference
    training_runs = {}
    for resolution in (1024, 1536):
        label = f"band_{resolution}"
        training = train_legend_model(
            data, output/label/"training", model=model, epochs=60, patience=10,
            imgsz=resolution, batch=4, device=device, seed=42, rect=True,
            model_factory=model_factory,
        )
        training_runs[label] = training
        detector = detector_factory(training["best_weights"])
        def refined(image_path, _detector=detector, _resolution=resolution, **_kwargs):
            return predictor(image_path, band_detector=_detector, band_imgsz=_resolution)
        reports[label] = evaluate_manifest(manifest, None, output/label/"validation", predictor=refined,
                                           imgsz=resolution, conf=policy.confidence, device=device, save_previews=True)
        del detector
        # Leave CUDA memory available for the next independent training run.
        if model_factory is None:
            import gc
            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        write_json(output/"training_runs.json", training_runs)
    summaries = {name: legend_summary(report) for name, report in reports.items()}
    selected = max(summaries, key=lambda name: (summaries[name]["iou_ge_0_8"], summaries[name]["iou_mean"]))
    selection = {"selected": selected, "results": summaries, "policy": policy.to_dict(),
                 "band_weights": training_runs[selected]["best_weights"] if selected != "reference" else None,
                 "band_imgsz": int(selected.split("_")[1]) if selected != "reference" else None,
                 "development_only": True}
    write_json(output/"comparison.json", selection)
    return selection
