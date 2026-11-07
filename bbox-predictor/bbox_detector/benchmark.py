"""Comparable native-document evaluation for YOLO, templates, and refinement.

Template references must be disjoint from the evaluated images and families.
Benchmarking refines cached YOLO outputs so paired changes isolate geometry.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
import statistics
from typing import Any, Callable, Mapping

from .common import CLASS_NAMES, write_json
from .evaluation import EDGES, _write_csv, aggregate_metrics, evaluate_manifest

BACKENDS = ("yolo", "classical", "hybrid")


def _json(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return payload


def validate_bank_split(
    manifest_path: str | Path, templates: str | Path, *, split: str = "val"
) -> dict[str, Any]:
    """Reject evaluated images/families in a bank, including incomplete provenance."""
    manifest = _json(manifest_path)
    records = [record for record in manifest["records"] if record.get("split") == split]
    if not records:
        raise ValueError(f"Manifest contains no documents for split {split!r}")
    bank = _json(Path(templates) / "bank.json")
    references = bank.get("references")
    if not isinstance(references, list) or not references:
        raise ValueError("Template bank needs nonempty references with training provenance")
    if any(not isinstance(reference, dict) or reference.get("split") != "train" or not reference.get("image_name") or not reference.get("group") for reference in references):
        raise ValueError("Every template reference must identify its image, family, and train split")
    reference_images = {reference["image_name"] for reference in references}
    reference_groups = {reference["group"] for reference in references}
    if set(bank.get("training_images", [])) != reference_images or set(bank.get("training_groups", [])) != reference_groups:
        raise ValueError("Template bank training provenance does not match its references")
    evaluated_images = {record["image_name"] for record in records}
    evaluated_groups = {record["group"] for record in records}
    leaked_images = sorted(reference_images & evaluated_images)
    leaked_groups = sorted(reference_groups & evaluated_groups)
    if leaked_images or leaked_groups:
        raise ValueError(f"Template bank leaks evaluation images or families: images={leaked_images}, groups={leaked_groups}")
    # A known manifest reference may not relabel validation data as training.
    by_name = {record["image_name"]: record for record in manifest["records"]}
    for reference in references:
        known = by_name.get(reference["image_name"])
        if known is not None and (known.get("split") != "train" or known.get("group") != reference["group"]):
            raise ValueError(f"Template reference conflicts with manifest provenance: {reference['image_name']}")
    return bank


def make_predictor(
    backend: str,
    *,
    weights: str | Path | None = None,
    templates: str | Path | None = None,
    device: str | None = None,
    imgsz: int = 1536,
    conf: float = 0.25,
) -> Callable[..., dict[str, Any]]:
    """Create one reusable image-only predictor; dependencies load when selected.

    Returned callables accept the inference kwargs passed by evaluate_manifest.
    ``weights`` in a subsequent call is ignored: the factory's model is reused.
    Diagnostics remain outside the standard prediction schema unless a caller
    explicitly supplies an output mapping.
    """
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend {backend!r}; choose from {BACKENDS}")
    default_device, default_imgsz, default_conf = device, imgsz, conf
    if backend == "classical":
        if templates is None:
            raise ValueError("The classical backend requires --templates")
        if weights is not None:
            raise ValueError("The classical backend does not use --weights")
        from .classical import ClassicalDetector

        detector = ClassicalDetector(templates)

        def predict(image_path: str | Path, *, diagnostics: dict | None = None, **_options: Any) -> dict[str, Any]:
            return detector.predict_document(image_path, diagnostics=diagnostics)

        return predict
    if weights is None:
        raise ValueError(f"The {backend} backend requires --weights")
    if templates is not None:
        raise ValueError(f"The {backend} backend does not use --templates")
    from .inference import UltralyticsDetector, predict_document

    detector = UltralyticsDetector(weights)
    if backend == "hybrid":
        from .vision import refine_prediction

    def predict(
        image_path: str | Path,
        *,
        weights: str | Path | None = None,
        device: str | None = default_device,
        imgsz: int = default_imgsz,
        confidence: float = default_conf,
        diagnostics: dict | None = None,
    ) -> dict[str, Any]:
        yolo_details: dict[str, Any] = {}
        prediction = predict_document(image_path, detector=detector, device=device, imgsz=imgsz, confidence=confidence, diagnostics=yolo_details)
        if backend == "hybrid":
            refinement_details: dict[str, Any] = {}
            prediction = refine_prediction(image_path, prediction, diagnostics=refinement_details)
            if diagnostics is not None:
                diagnostics.update(backend=backend, yolo=yolo_details, refinement=refinement_details)
        elif diagnostics is not None:
            diagnostics.update(yolo_details, backend=backend)
        return prediction

    return predict


def evaluate_backend(
    manifest_path: str | Path,
    output_dir: str | Path,
    *,
    backend: str = "yolo",
    weights: str | Path | None = None,
    templates: str | Path | None = None,
    predictions_dir: str | Path | None = None,
    split: str = "val",
    device: str | None = None,
    imgsz: int = 1536,
    conf: float = 0.25,
    iou_threshold: float = 0.8,
    save_previews: bool = True,
    predictor: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Select a backend while reusing the existing full-document metric contract."""
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend: {backend}")
    if predictions_dir is not None:
        if weights is not None or templates is not None or predictor is not None:
            raise ValueError("Precomputed predictions cannot be combined with weights, templates, or a predictor")
        report = evaluate_manifest(manifest_path, None, output_dir, split=split, predictions_dir=predictions_dir, device=device, imgsz=imgsz, conf=conf, iou_threshold=iou_threshold, save_previews=save_previews)
    else:
        if backend == "classical":
            if templates is None:
                raise ValueError("The classical backend requires --templates")
            if weights is not None:
                raise ValueError("The classical backend does not use --weights")
            validate_bank_split(manifest_path, templates, split=split)
        elif templates is not None:
            raise ValueError(f"The {backend} backend does not use --templates")
        if predictor is None:
            selected = make_predictor(backend, weights=weights, templates=templates, device=device, imgsz=imgsz, conf=conf)

            def predictor(image_path: Path, **options: Any) -> Mapping[str, Any]:
                diagnostics: dict[str, Any] = {}
                prediction = selected(image_path, diagnostics=diagnostics, **options)
                return {**prediction, "diagnostics": diagnostics}

        report = evaluate_manifest(manifest_path, None, output_dir, split=split, predictor=predictor, device=device, imgsz=imgsz, conf=conf, iou_threshold=iou_threshold, save_previews=save_previews)
    report["backend"] = backend
    report["weights"] = str(weights) if weights is not None else None
    report["templates"] = str(Path(templates).resolve()) if templates is not None else None
    if predictions_dir is not None:
        report["backend_note"] = "Backend is a caller-supplied label for precomputed files; no detector or template bank was inspected."
    write_json(Path(output_dir) / "report.json", report)
    return report


def _matching_documents(first: Mapping[str, Any], second: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if first["split"] != second["split"]:
        raise ValueError("Cannot compare reports from different splits")
    left = {document["image_name"]: document for document in first["documents"]}
    right = {document["image_name"]: document for document in second["documents"]}
    if len(left) != len(first["documents"]) or len(right) != len(second["documents"]):
        raise ValueError("Cannot compare reports with duplicate image names")
    if set(left) != set(right):
        raise ValueError("Cannot compare reports with different evaluated image sets")
    return left, right


def paired_comparison(yolo_report: Mapping[str, Any], hybrid_report: Mapping[str, Any]) -> dict[str, Any]:
    """Count paired native-IoU changes, retaining failed pages in both reports."""
    baseline, hybrid = _matching_documents(yolo_report, hybrid_report)
    counts = {"improved": 0, "regressed": 0, "equal": 0}
    class_counts = {name: dict(counts) for name in CLASS_NAMES}
    passes = {"gained": 0, "lost": 0, "unchanged": 0}
    pages = []

    def label(delta: float) -> str:
        return "equal" if abs(delta) < 1e-9 else ("improved" if delta > 0 else "regressed")

    for image_name, original in baseline.items():
        refined = hybrid[image_name]
        changes = {name: refined["metrics"]["regions"][name]["iou"] - original["metrics"]["regions"][name]["iou"] for name in CLASS_NAMES}
        change = label(statistics.fmean(changes.values()))
        counts[change] += 1
        for name, delta in changes.items():
            class_counts[name][label(delta)] += 1
        before = original["metrics"]["all_regions_iou_ge_threshold"]
        after = refined["metrics"]["all_regions_iou_ge_threshold"]
        transition = "unchanged" if before == after else ("gained" if after else "lost")
        passes[transition] += 1
        pages.append({"image_name": image_name, "mean_iou_change": statistics.fmean(changes.values()), "change": change, "region_iou_changes": changes, "all_regions_pass_change": transition})
    return {"documents": len(pages), "criterion": "Change in mean native-document IoU across the three classes; also inspect per-class regressions", "document_mean_iou": counts, "classes": class_counts, "all_regions_pass": passes, "per_document": pages}


def _cached_hybrid_predictor(prediction_dir: Path) -> Callable[..., dict[str, Any]]:
    from .inference import InferenceError
    from .vision import refine_prediction

    def predict(image_path: Path, **_options: Any) -> dict[str, Any]:
        original = _json(prediction_dir / f"{image_path.stem}.json")
        boxes = original.get("bounding_boxes", {})
        if any(name not in boxes for name in CLASS_NAMES):
            raise InferenceError("YOLO regions unresolved; hybrid cannot invent missing regions", partial_boxes=boxes, diagnostics={"backend": "hybrid", "cached_yolo": True})
        # Evaluation artifacts may contain annotation-error metadata. Only the
        # detector's standard four fields may enter geometric refinement.
        prediction = {key: original[key] for key in ("image_name", "width", "height", "bounding_boxes")}
        diagnostics: dict[str, Any] = {}
        refined = refine_prediction(image_path, prediction, diagnostics=diagnostics)
        return {**refined, "diagnostics": {"backend": "hybrid", "cached_yolo": True, "refinement": diagnostics}}

    return predict


def _combine_hybrid_runtime(hybrid: dict[str, Any], yolo: Mapping[str, Any], output: Path) -> None:
    yolo_pages, hybrid_pages = _matching_documents(yolo, hybrid)
    for image_name, document in hybrid_pages.items():
        document["refinement_runtime_seconds"] = document["runtime_seconds"]
        document["yolo_runtime_seconds"] = yolo_pages[image_name]["runtime_seconds"]
        document["runtime_seconds"] += document["yolo_runtime_seconds"]
        document["processing_seconds"] += document["yolo_runtime_seconds"]
    hybrid["summary"] = aggregate_metrics(hybrid["documents"], iou_threshold=hybrid["settings"]["iou_threshold"])
    hybrid["metric_notes"]["runtime_seconds"] = "Per-page measured YOLO prediction time plus cached-output loading/refinement time; YOLO runs once for exact paired comparisons. processing_seconds includes this base prediction time and hybrid comparisons. Model loading excluded."
    hybrid["hybrid_base"] = "Cached YOLO predictions; native coordinates are identical before refinement"
    write_json(output / "report.json", hybrid)
    _write_csv(output / "per_document.csv", hybrid["documents"])


def _benchmark_csv(path: Path, backends: Mapping[str, Mapping[str, Any]]) -> None:
    fields = ["backend", "status", "reason", "documents", "all_three_pass_count", "all_three_pass_fraction", "documents_with_errors", "runtime_seconds_total", "runtime_seconds_median"]
    fields += [f"{name}_{metric}" for name in CLASS_NAMES for metric in ("iou_mean", "iou_p10", "missing_rate", *[f"{edge}_error_p90_px" for edge in EDGES])]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for backend, result in backends.items():
            row = {"backend": backend, "status": result["status"], "reason": result.get("reason", "")}
            if result["status"] == "evaluated":
                summary = result["summary"]
                row.update(documents=summary["documents"], all_three_pass_count=summary["all_regions_iou_ge_threshold"]["documents"], all_three_pass_fraction=summary["all_regions_iou_ge_threshold"]["fraction"], documents_with_errors=summary["documents_with_errors"], runtime_seconds_total=summary["runtime_seconds"]["total"], runtime_seconds_median=summary["runtime_seconds"]["median"])
                for name, metrics in summary["classes"].items():
                    for metric in ("iou_mean", "iou_p10", "missing_rate"):
                        row[f"{name}_{metric}"] = metrics[metric]
                    for edge in EDGES:
                        row[f"{name}_{edge}_error_p90_px"] = metrics["edge_absolute_error_px"][edge]["p90"]
            writer.writerow(row)


def benchmark_manifest(
    manifest_path: str | Path,
    output_dir: str | Path,
    *,
    templates: str | Path | None = None,
    weights: str | Path | None = None,
    split: str = "val",
    device: str | None = None,
    imgsz: int = 1536,
    conf: float = 0.25,
    iou_threshold: float = 0.8,
    save_previews: bool = True,
    predictors: Mapping[str, Callable[..., Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Evaluate available backends on identical pages; report setup failures openly.

    Optional injected predictors support deterministic integration tests. With
    real detectors, hybrid always consumes the exact cached YOLO predictions.
    Test-set reports are never used to select or enable a backend.
    """
    if split not in {"train", "val", "test"}:
        raise ValueError("split must be train, val, or test")
    if not 0 < iou_threshold <= 1:
        raise ValueError("iou_threshold must be in (0, 1]")
    if templates is None and weights is None and not predictors:
        raise ValueError("Provide a template bank or trained weights to benchmark")
    manifest = _json(manifest_path)
    records = [record for record in manifest["records"] if record.get("split") == split]
    if not records:
        raise ValueError(f"Manifest contains no documents for split {split!r}")
    if len({record["image_name"] for record in records}) != len(records):
        raise ValueError("Evaluation image names must be unique")
    bank = validate_bank_split(manifest_path, templates, split=split) if templates is not None else {}
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    supplied = dict(predictors or {})
    if set(supplied) - set(BACKENDS):
        raise ValueError("Injected predictor names must be yolo, classical, or hybrid")
    results: dict[str, Any] = {}
    reports: dict[str, Any] = {}
    for backend in ("classical", "yolo", "hybrid"):
        if backend == "classical" and templates is None:
            results[backend] = {"status": "unavailable", "reason": "No --templates supplied"}
            continue
        if backend != "classical" and weights is None and backend not in supplied:
            results[backend] = {"status": "unavailable", "reason": "No --weights supplied; no YOLO inference or training was attempted"}
            continue
        predictor = supplied.get(backend)
        if backend == "hybrid" and predictor is None and "yolo" not in reports:
            results[backend] = {"status": "unavailable", "reason": "YOLO evaluation unavailable; hybrid needs its predictions"}
            continue
        try:
            if backend == "hybrid" and predictor is None:
                predictor = _cached_hybrid_predictor(output / "yolo" / "predictions")
            report = evaluate_backend(manifest_path, output / backend, backend=backend, weights=weights if backend != "classical" else None, templates=templates if backend == "classical" else None, split=split, device=device, imgsz=imgsz, conf=conf, iou_threshold=iou_threshold, save_previews=save_previews, predictor=predictor)
            if backend == "hybrid" and backend not in supplied:
                _combine_hybrid_runtime(report, reports["yolo"], output / backend)
            if reports:
                _matching_documents(next(iter(reports.values())), report)
            reports[backend] = report
            results[backend] = {"status": "evaluated", "report": f"{backend}/report.json", "summary": report["summary"]}
        except (OSError, ValueError, RuntimeError, ImportError, KeyError) as exc:
            results[backend] = {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}
    paired = paired_comparison(reports["yolo"], reports["hybrid"]) if "yolo" in reports and "hybrid" in reports else {"status": "unavailable", "reason": "Both YOLO and hybrid reports are required"}
    ranked = sorted(reports, key=lambda backend: (reports[backend]["summary"]["all_regions_iou_ge_threshold"]["fraction"], min(reports[backend]["summary"]["classes"][name]["iou_p10"] for name in CLASS_NAMES)), reverse=True) if split == "val" else []
    benchmark = {
        "manifest": str(Path(manifest_path).resolve()),
        "split": split,
        "image_names": [record["image_name"] for record in manifest["records"] if record.get("split") == split],
        "template_bank": str(Path(templates).resolve()) if templates is not None else None,
        "template_source_manifest_sha256": bank.get("source_manifest_sha256"),
        "settings": {"imgsz": imgsz, "confidence": conf, "iou_threshold": iou_threshold, "device": device},
        "backends": results,
        "validation_ranking": ranked,
        "ranking_note": "Only identical image sets/splits are compared. Ranking is descriptive on validation, ordered by all-three pass fraction then weakest-class IoU p10. No backend is automatically enabled; test reports do not select a backend.",
        "paired_yolo_hybrid": paired,
    }
    write_json(output / "benchmark.json", benchmark)
    _benchmark_csv(output / "benchmark.csv", results)
    return benchmark


def benchmark_backends(
    manifest_path: str | Path,
    templates: str | Path,
    output_dir: str | Path,
    **options: Any,
) -> dict[str, Any]:
    """Compatibility alias; new callers should use benchmark_manifest."""
    return benchmark_manifest(manifest_path, output_dir, templates=templates, **options)
