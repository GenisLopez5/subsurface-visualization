#!/usr/bin/env python3
"""Benchmark the packaged full workflow on an annotated test ZIP or directory.

Inference receives only each image. Its annotation is opened after prediction,
then used for native-pixel IoU and edge-error scoring. Models are loaded once
and reused sequentially for every document.
"""
from __future__ import annotations

import argparse
import csv
from contextlib import redirect_stdout
import hashlib
import html
import json
import math
import numbers
import os
from pathlib import Path, PurePosixPath
import platform
import statistics
import sys
import tempfile
import threading
import time
import traceback
from typing import Any, Mapping, Sequence
from urllib.parse import quote
import zipfile

from bbox_runtime import BoundingBoxPredictor
from bbox_runtime.common import CLASS_NAMES, open_scan, write_json


ROOT = Path(__file__).resolve().parent
IMAGE_SUFFIXES = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}
EDGES = ("xmin", "ymin", "xmax", "ymax")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100.0
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def box_iou(first: Sequence[float], second: Sequence[float]) -> float:
    intersection = max(0.0, min(first[2], second[2]) - max(first[0], second[0])) * max(
        0.0, min(first[3], second[3]) - max(first[1], second[1])
    )
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    union = first_area + second_area - intersection
    return intersection / union if union > 0 else 0.0


def document_metrics(
    truth: Mapping[str, Sequence[float]],
    predicted: Mapping[str, Sequence[float]],
    *,
    threshold: float,
) -> dict[str, Any]:
    regions: dict[str, Any] = {}
    for name in CLASS_NAMES:
        expected, actual = truth.get(name), predicted.get(name)
        present = expected is not None and actual is not None
        regions[name] = {
            "iou": box_iou(expected, actual) if present else 0.0,
            "missing": actual is None,
            "edge_absolute_error_px": {
                edge: abs(float(actual[index]) - float(expected[index]))
                for index, edge in enumerate(EDGES)
            }
            if present
            else None,
        }
    return {
        "regions": regions,
        "all_regions_iou_ge_threshold": all(
            region["iou"] >= threshold for region in regions.values()
        ),
    }


def aggregate_metrics(
    documents: Sequence[Mapping[str, Any]], *, threshold: float
) -> dict[str, Any]:
    count = len(documents)
    classes: dict[str, Any] = {}
    for name in CLASS_NAMES:
        regions = [document["metrics"]["regions"][name] for document in documents]
        ious = [region["iou"] for region in regions]
        edge_errors = [
            region["edge_absolute_error_px"]
            for region in regions
            if region["edge_absolute_error_px"] is not None
        ]
        missing = sum(region["missing"] for region in regions)
        classes[name] = {
            "documents": count,
            "present_predictions": count - missing,
            "missing_predictions": missing,
            "missing_rate": missing / count if count else None,
            "iou_mean": statistics.fmean(ious) if ious else None,
            "iou_median": statistics.median(ious) if ious else None,
            "iou_p10": _percentile(ious, 10),
            "iou_ge_0_8": sum(value >= 0.8 for value in ious),
            "iou_ge_0_9": sum(value >= 0.9 for value in ious),
            "edge_error_count": len(edge_errors),
            "edge_absolute_error_px": {
                edge: {
                    "mean": statistics.fmean([errors[edge] for errors in edge_errors])
                    if edge_errors
                    else None,
                    "median": statistics.median([errors[edge] for errors in edge_errors])
                    if edge_errors
                    else None,
                    "p90": _percentile([errors[edge] for errors in edge_errors], 90),
                }
                for edge in EDGES
            },
        }
    runtimes = [float(document["runtime_seconds"]) for document in documents]
    memory = [
        float(document["peak_process_rss_mb"])
        for document in documents
        if document.get("peak_process_rss_mb") is not None
    ]
    passed = sum(document["metrics"]["all_regions_iou_ge_threshold"] for document in documents)
    return {
        "documents": count,
        "classes": classes,
        "all_regions_iou_ge_threshold": {
            "threshold": threshold,
            "documents": passed,
            "fraction": passed / count if count else None,
        },
        "region_missing_rate": (
            sum(classes[name]["missing_predictions"] for name in CLASS_NAMES)
            / (count * len(CLASS_NAMES))
            if count
            else None
        ),
        "documents_with_inference_errors": sum(
            document["inference_error"] is not None for document in documents
        ),
        "runtime_seconds": {
            "total": sum(runtimes),
            "mean": statistics.fmean(runtimes) if runtimes else None,
            "median": statistics.median(runtimes) if runtimes else None,
            "p90": _percentile(runtimes, 90),
        },
        "peak_process_rss_mb_max": max(memory) if memory else None,
    }


class MemorySampler:
    """Sample process RSS during inference; this deliberately excludes VRAM."""

    def __init__(self) -> None:
        self.peak_mb: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.method = "unavailable"
        try:
            self._page_size = os.sysconf("SC_PAGE_SIZE")
            with open("/proc/self/statm", encoding="ascii") as stream:
                int(stream.read().split()[1])
            self.method = "process_rss_sampled_50ms"
        except (OSError, ValueError, IndexError, AttributeError):
            self._page_size = 0
            try:
                import resource

                resource.getrusage(resource.RUSAGE_SELF)
                self.method = "process_lifetime_peak_rss"
            except (ImportError, OSError, ValueError):
                pass

    def _sample(self) -> None:
        try:
            if self.method == "process_rss_sampled_50ms":
                with open("/proc/self/statm", encoding="ascii") as stream:
                    value = int(stream.read().split()[1]) * self._page_size / (1024 * 1024)
            elif self.method == "process_lifetime_peak_rss":
                import resource

                raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                value = raw / (1024 * 1024) if sys.platform == "darwin" else raw / 1024
            else:
                return
            self.peak_mb = max(self.peak_mb or 0.0, value)
        except (OSError, ValueError, IndexError):
            return

    def _run(self) -> None:
        while not self._stop.wait(0.05):
            self._sample()

    def __enter__(self) -> "MemorySampler":
        self._sample()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        self._sample()


def _annotation_dir(root: Path) -> Path | None:
    matches = [path for path in (root / "annotations", root / "bbox_annotation") if path.is_dir()]
    if len(matches) > 1:
        raise ValueError(f"Dataset has both annotations/ and bbox_annotation/: {root}")
    return matches[0] if matches else None


def _discover_directory(path: Path) -> tuple[Path, Path, list[Path]]:
    candidates = [path]
    candidates.extend(item.parent for item in path.rglob("images") if item.is_dir())
    valid = []
    for root in dict.fromkeys(item.resolve() for item in candidates):
        images = root / "images"
        annotations = _annotation_dir(root)
        if images.is_dir() and annotations is not None:
            valid.append((root, annotations))
    if len(valid) != 1:
        raise ValueError(
            f"Expected exactly one dataset containing images/ and annotations/; found {len(valid)} in {path}"
        )
    root, annotations = valid[0]
    images = sorted(
        item for item in (root / "images").iterdir()
        if item.is_file() and item.suffix.lower() in IMAGE_SUFFIXES
    )
    if not images:
        raise ValueError(f"No supported images found in {root / 'images'}")
    stems = [image.stem for image in images]
    if len(stems) != len(set(stems)):
        raise ValueError("Image stems must be unique so annotations can be matched safely")
    missing = [image.name for image in images if not (annotations / f"{image.stem}.json").is_file()]
    if missing:
        raise ValueError(f"Missing annotations for images: {missing}")
    return root, annotations, images


def _safe_zip_parts(name: str) -> tuple[str, ...]:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe ZIP member: {name}")
    return tuple(part for part in path.parts if part not in ("", "."))


def _extract_zip(archive: Path, destination: Path) -> Path:
    with zipfile.ZipFile(archive) as handle:
        members = [(info, _safe_zip_parts(info.filename)) for info in handle.infolist() if not info.is_dir()]
        roots: set[tuple[str, ...]] = set()
        for _info, parts in members:
            for index, part in enumerate(parts):
                if part == "images" and index + 1 < len(parts):
                    roots.add(parts[:index])
        valid = []
        for root in roots:
            prefixes = {parts[len(root)] for _info, parts in members if parts[:len(root)] == root and len(parts) > len(root)}
            annotation_names = prefixes & {"annotations", "bbox_annotation"}
            if "images" in prefixes and len(annotation_names) == 1:
                valid.append((root, next(iter(annotation_names))))
        if len(valid) != 1:
            raise ValueError(
                f"Expected exactly one dataset containing images/ and annotations/ in ZIP; found {len(valid)}"
            )
        root, annotation_name = valid[0]
        selected = []
        for info, parts in members:
            if parts[:len(root)] != root or len(parts) != len(root) + 2:
                continue
            folder, filename = parts[-2], parts[-1]
            suffix = Path(filename).suffix.lower()
            if (folder == "images" and suffix in IMAGE_SUFFIXES) or (
                folder == annotation_name and suffix == ".json"
            ):
                selected.append((info, folder, filename))
        for info, folder, filename in selected:
            target = destination / folder / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            with handle.open(info) as source, target.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
    return destination


def _load_truth(annotation_path: Path, image_path: Path, result: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = json.loads(annotation_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("bounding_boxes"), dict):
        raise ValueError(f"Invalid annotation object: {annotation_path}")
    if payload.get("image_name") != image_path.name:
        raise ValueError(f"Annotation image_name mismatch: {annotation_path}")
    width, height = payload.get("width"), payload.get("height")
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in (width, height)):
        raise ValueError(f"Invalid annotation dimensions: {annotation_path}")
    with open_scan(image_path) as scan:
        if (width, height) != (scan.width, scan.height):
            raise ValueError(f"Annotation/image dimensions differ: {annotation_path}")
    if result is not None and (
        result.get("image_name") != image_path.name
        or result.get("width") != width
        or result.get("height") != height
    ):
        raise ValueError(f"Prediction metadata differs from annotation: {image_path.name}")
    boxes = payload["bounding_boxes"]
    for name in CLASS_NAMES:
        box = boxes.get(name)
        if (
            not isinstance(box, list)
            or len(box) != 4
            or any(isinstance(value, bool) or not isinstance(value, numbers.Real) for value in box)
            or not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)
        ):
            raise ValueError(f"Invalid {name} annotation in {annotation_path}")
    return payload


def _environment() -> dict[str, Any]:
    versions: dict[str, Any] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    for module_name in ("torch", "ultralytics", "openvino", "pyvips"):
        try:
            module = __import__(module_name)
            versions[module_name] = getattr(module, "__version__", None)
        except Exception as error:
            versions[module_name] = f"unavailable: {type(error).__name__}"
    return versions


def _select_models(choice: str, custom: Path | None, device: str) -> tuple[Path, str, str]:
    if custom is not None:
        models = custom.expanduser().resolve()
        if not models.is_dir():
            raise FileNotFoundError(f"Models directory does not exist: {models}")
        return models, device, "custom"
    int8 = ROOT / "models"
    pt = ROOT / "models-pt"

    def pt_ready() -> bool:
        try:
            manifest = json.loads((pt / "manifest.json").read_text(encoding="utf-8"))
            return all((pt / item["file"]).is_file() for item in manifest["models"].values())
        except (OSError, ValueError, KeyError, TypeError):
            return False

    if choice == "pt":
        if not pt_ready():
            raise FileNotFoundError(
                f"PT models are incomplete in {pt}; expected general.pt and legend.pt matching manifest.json"
            )
        return pt, device, "pt"
    if choice == "int8":
        return int8, "cpu" if device == "auto" else device, "int8"
    normalized_device = device.strip().lower()
    if normalized_device not in ("", "auto"):
        if normalized_device.startswith(("cpu", "intel")):
            return int8, device, "int8"
        if not pt_ready():
            raise FileNotFoundError(
                f"A GPU device was requested, but PT models are incomplete in {pt}"
            )
        return pt, device, "pt"
    try:
        import torch

        cuda = torch.cuda.is_available()
    except Exception:
        cuda = False
    if cuda and pt_ready():
        return pt, "0" if device == "auto" else device, "pt"
    return int8, "cpu" if device == "auto" else device, "int8"


def _write_csv(path: Path, documents: Sequence[Mapping[str, Any]]) -> None:
    fields = ["image_name", "status", "runtime_seconds", "processing_seconds", "peak_process_rss_mb"]
    fields += [f"{name}_iou" for name in CLASS_NAMES]
    fields += ["all_regions_pass", "inference_error"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for document in documents:
            writer.writerow({
                "image_name": document["image_name"],
                "status": document["status"],
                "runtime_seconds": document["runtime_seconds"],
                "processing_seconds": document["processing_seconds"],
                "peak_process_rss_mb": document["peak_process_rss_mb"],
                **{
                    f"{name}_iou": document["metrics"]["regions"][name]["iou"]
                    for name in CLASS_NAMES
                },
                "all_regions_pass": document["metrics"]["all_regions_iou_ge_threshold"],
                "inference_error": document["inference_error"] or "",
            })


def _fmt(value: Any, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _write_html(path: Path, report: Mapping[str, Any]) -> None:
    summary = report["summary"]
    class_rows = "".join(
        "<tr>"
        f"<td>{html.escape(name)}</td>"
        f"<td>{_fmt(summary['classes'][name]['iou_mean'])}</td>"
        f"<td>{_fmt(summary['classes'][name]['iou_median'])}</td>"
        f"<td>{_fmt(summary['classes'][name]['iou_p10'])}</td>"
        f"<td>{summary['classes'][name]['iou_ge_0_8']}/{summary['classes'][name]['documents']}</td>"
        f"<td>{summary['classes'][name]['missing_predictions']}</td>"
        "</tr>"
        for name in CLASS_NAMES
    )
    document_rows = []
    for document in report["documents"]:
        metrics = document["metrics"]
        passed = metrics["all_regions_iou_ge_threshold"]
        stem = Path(document["image_name"]).stem
        prediction_link = (
            f'<a href="{quote("predictions/" + stem + ".json")}">prediction</a>'
            if document["status"] == "ok"
            else f'<a href="{quote("errors/" + stem + ".json")}">error</a>'
        )
        document_rows.append(
            f'<tr class="{"pass" if passed else "fail"}">'
            f"<td>{html.escape(document['image_name'])}</td>"
            f"<td>{html.escape(document['status'])}</td>"
            f"<td>{_fmt(document['runtime_seconds'], 1)}</td>"
            f"<td>{_fmt(document['peak_process_rss_mb'], 1)}</td>"
            + "".join(f"<td>{_fmt(metrics['regions'][name]['iou'])}</td>" for name in CLASS_NAMES)
            + f"<td>{'yes' if passed else 'no'}</td><td>{prediction_link} · "
            f'<a href="{quote("diagnostics/" + stem + ".json")}">diagnostics</a></td></tr>'
        )
    passed = summary["all_regions_iou_ge_threshold"]
    page = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Bounding-box benchmark</title>
<style>
body{{font-family:system-ui,sans-serif;margin:24px;line-height:1.4;color:#202124}}
table{{border-collapse:collapse;width:100%;margin:16px 0 28px}}
th,td{{border:1px solid #ddd;padding:8px;text-align:right}} th:first-child,td:first-child{{text-align:left}}
th{{background:#f5f5f5}} tr.fail{{background:#fff1f1}} tr.pass{{background:#f3fff5}}
code{{background:#f5f5f5;padding:2px 5px}} .meta{{color:#555}}
</style></head><body>
<h1>Full-workflow bounding-box benchmark</h1>
<p class="meta">Status: {html.escape(report['status'])} · device: <code>{html.escape(report['device'])}</code>
· model set: <code>{html.escape(report['model_set'])}</code> · threshold: {passed['threshold']}</p>
<p><strong>{passed['documents']}/{summary['documents']}</strong> documents have all three regions at or above the IoU threshold.
Inference time: {_fmt(summary['runtime_seconds']['total'], 1)} s total,
{_fmt(summary['runtime_seconds']['mean'], 1)} s mean. Peak sampled RSS: {_fmt(summary['peak_process_rss_mb_max'], 1)} MB.</p>
<p><a href="report.json">Full JSON</a> · <a href="per_document.csv">CSV</a> ·
<a href="inputs.json">Input provenance</a></p>
<h2>Aggregate metrics</h2>
<table><thead><tr><th>Region</th><th>IoU mean</th><th>IoU median</th><th>IoU p10</th><th>IoU ≥ 0.8</th><th>Missing</th></tr></thead>
<tbody>{class_rows}</tbody></table>
<h2>Per document</h2>
<table><thead><tr><th>Document</th><th>Status</th><th>Seconds</th><th>RSS MB</th>
<th>Depth IoU</th><th>Lithology IoU</th><th>Legend IoU</th><th>Pass</th><th>Artifacts</th></tr></thead>
<tbody>{''.join(document_rows)}</tbody></table>
<p class="meta">Annotations are read only after inference for each image. Per-document time excludes one-time model loading and scoring/report writes. RSS excludes GPU VRAM.</p>
</body></html>"""
    path.write_text(page, encoding="utf-8")


def _save_report(
    output: Path,
    *,
    status: str,
    documents: Sequence[Mapping[str, Any]],
    total_images: int,
    current_image: str | None,
    threshold: float,
    predictor: BoundingBoxPredictor,
    model_set: str,
    models_dir: Path,
    model_loading_seconds: float,
    benchmark_started: float,
    environment: Mapping[str, Any],
) -> dict[str, Any]:
    report = {
        "version": 1,
        "status": status,
        "fresh_inference": True,
        "cached_predictions_used": False,
        "annotation_access": "Each annotation is opened only after inference completes for that image.",
        "model_set": model_set,
        "models_dir": str(models_dir),
        "device": predictor.device,
        "workflow_version": predictor.version,
        "models_sha256": predictor.model_hashes,
        "policy": predictor.policy.to_dict(),
        "model_loading_seconds": model_loading_seconds,
        "total_images": total_images,
        "completed_images": len(documents),
        "current_image": current_image,
        "elapsed_seconds": time.perf_counter() - benchmark_started,
        "environment": dict(environment),
        "metric_notes": {
            "iou": "Native-image intersection over union; failed or missing regions score zero.",
            "edge_absolute_error_px": "Absolute native-pixel error for xmin, ymin, xmax and ymax when prediction is present.",
            "runtime_seconds": "Fresh end-to-end predictor.predict wall time; model loading, annotation access and reporting excluded.",
            "peak_process_rss_mb": "Process RSS sampled during inference; GPU VRAM is not included.",
        },
        "summary": aggregate_metrics(documents, threshold=threshold),
        "documents": list(documents),
    }
    write_json(output / "report.json", report)
    _write_csv(output / "per_document.csv", documents)
    _write_html(output / "index.html", report)
    return report


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    dataset = args.dataset.expanduser().resolve()
    if not dataset.exists():
        raise FileNotFoundError(f"Dataset does not exist: {dataset}")
    output = args.output.expanduser().resolve()
    if dataset.is_dir() and (output == dataset or dataset in output.parents):
        raise ValueError("Output directory must be outside the input dataset")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Output directory is not empty; refusing to overwrite: {output}")
    output.mkdir(parents=True, exist_ok=True)
    benchmark_started = time.perf_counter()
    environment = _environment()
    models_dir, device, model_set = _select_models(args.models, args.models_dir, args.device)

    with tempfile.TemporaryDirectory(prefix="bbox-benchmark-") as temporary:
        if dataset.is_file():
            if not zipfile.is_zipfile(dataset):
                raise ValueError(f"Dataset file is not a ZIP archive: {dataset}")
            extracted = _extract_zip(dataset, Path(temporary) / "dataset")
            root, annotations, images = _discover_directory(extracted)
            source = {"type": "zip", "path": str(dataset), "sha256": _sha256(dataset)}
        else:
            root, annotations, images = _discover_directory(dataset)
            source = {"type": "directory", "path": str(dataset)}
        if args.limit is not None:
            images = images[:args.limit]
        inputs = {
            "source": source,
            "dataset_root": str(root),
            "images": [image.name for image in images],
            "annotation_directory_name": annotations.name,
            "models": args.models,
            "models_dir": str(models_dir),
            "device_requested": args.device,
            "cpu_threads": args.cpu_threads,
            "iou_threshold": args.iou_threshold,
        }
        write_json(output / "inputs.json", inputs)

        print(f"Loading {model_set} full workflow from {models_dir} on {device}...", flush=True)
        loading_started = time.perf_counter()
        with redirect_stdout(sys.stderr):
            predictor = BoundingBoxPredictor(
                device=device,
                cpu_threads=args.cpu_threads,
                models_dir=models_dir,
            )
        model_loading_seconds = time.perf_counter() - loading_started
        documents: list[dict[str, Any]] = []
        _save_report(
            output,
            status="running",
            documents=documents,
            total_images=len(images),
            current_image=None,
            threshold=args.iou_threshold,
            predictor=predictor,
            model_set=model_set,
            models_dir=models_dir,
            model_loading_seconds=model_loading_seconds,
            benchmark_started=benchmark_started,
            environment=environment,
        )
        for index, image in enumerate(images, 1):
            annotation_path = annotations / f"{image.stem}.json"
            print(f"[{index}/{len(images)}] {image.name}", flush=True)
            _save_report(
                output,
                status="running",
                documents=documents,
                total_images=len(images),
                current_image=image.name,
                threshold=args.iou_threshold,
                predictor=predictor,
                model_set=model_set,
                models_dir=models_dir,
                model_loading_seconds=model_loading_seconds,
                benchmark_started=benchmark_started,
                environment=environment,
            )
            details: dict[str, Any] = {}
            inference_error: str | None = None
            result: dict[str, Any] | None = None
            predicted: Mapping[str, Sequence[float]] = {}
            processing_started = time.perf_counter()
            inference_started = time.perf_counter()
            with MemorySampler() as memory:
                try:
                    with redirect_stdout(sys.stderr):
                        result = predictor.predict(image, diagnostics=details)
                    predicted = result["bounding_boxes"]
                except Exception as error:  # the report must retain failed pages in its denominator
                    inference_error = f"{type(error).__name__}: {error}"
                    predicted = getattr(error, "partial_boxes", {}) or {}
                    write_json(output / "errors" / f"{image.stem}.json", {
                        "image_name": image.name,
                        "error": inference_error,
                        "partial_boxes": predicted,
                        "traceback": traceback.format_exc(),
                    })
            runtime = time.perf_counter() - inference_started
            write_json(output / "diagnostics" / f"{image.stem}.json", details)

            # Ground truth enters only here, after fresh image-only inference.
            annotation = _load_truth(annotation_path, image, result)
            truth = annotation["bounding_boxes"]
            metrics = document_metrics(truth, predicted, threshold=args.iou_threshold)
            write_json(output / "ground_truth" / f"{image.stem}.json", {
                "image_name": image.name,
                "width": annotation["width"],
                "height": annotation["height"],
                "bounding_boxes": truth,
            })
            if result is not None:
                write_json(output / "predictions" / f"{image.stem}.json", result)
            status = "ok" if inference_error is None else ("partial" if predicted else "error")
            document = {
                "image_name": image.name,
                "status": status,
                "inference_error": inference_error,
                "runtime_seconds": runtime,
                "processing_seconds": time.perf_counter() - processing_started,
                "peak_process_rss_mb": memory.peak_mb,
                "memory_measurement": memory.method,
                "metrics": metrics,
                "truth_boxes": truth,
                "predicted_boxes": dict(predicted),
                "diagnostics_file": f"diagnostics/{image.stem}.json",
                "prediction_file": f"predictions/{image.stem}.json" if result is not None else None,
            }
            documents.append(document)
            _save_report(
                output,
                status="running",
                documents=documents,
                total_images=len(images),
                current_image=None,
                threshold=args.iou_threshold,
                predictor=predictor,
                model_set=model_set,
                models_dir=models_dir,
                model_loading_seconds=model_loading_seconds,
                benchmark_started=benchmark_started,
                environment=environment,
            )
            scores = {name: round(metrics["regions"][name]["iou"], 4) for name in CLASS_NAMES}
            print(
                f"  {status} in {runtime:.1f}s; IoU={json.dumps(scores)}; "
                f"all-pass={metrics['all_regions_iou_ge_threshold']}",
                flush=True,
            )
        report = _save_report(
            output,
            status="finished",
            documents=documents,
            total_images=len(images),
            current_image=None,
            threshold=args.iou_threshold,
            predictor=predictor,
            model_set=model_set,
            models_dir=models_dir,
            model_loading_seconds=model_loading_seconds,
            benchmark_started=benchmark_started,
            environment=environment,
        )
    print(f"Finished. Open {output / 'index.html'}", flush=True)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "dataset",
        type=Path,
        help="Sponsor ZIP, or a directory containing images/ and annotations/",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs" / time.strftime("benchmark-%Y%m%d-%H%M%S"),
        help="New/empty report directory (default: outputs/benchmark-<timestamp>)",
    )
    parser.add_argument(
        "--models",
        choices=("auto", "int8", "pt"),
        default="auto",
        help="auto: PT on CUDA when available, otherwise packaged INT8 (default)",
    )
    parser.add_argument("--models-dir", type=Path, help="Custom model directory; overrides --models")
    parser.add_argument("--device", default="auto", help="auto, cpu, 0, intel:gpu, etc.")
    parser.add_argument("--cpu-threads", type=int, default=2, help="PyTorch CPU threads (default: 2)")
    parser.add_argument("--iou-threshold", type=float, default=0.8)
    parser.add_argument("--limit", type=int, help="Run only the first N images (smoke tests only)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 0 < args.iou_threshold <= 1:
        parser.error("--iou-threshold must be in (0, 1]")
    if args.cpu_threads < 1:
        parser.error("--cpu-threads must be positive")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    try:
        run_benchmark(args)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        print(f"Benchmark failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
