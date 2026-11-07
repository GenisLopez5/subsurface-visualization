"""Score final, original-document boxes and save bounded comparison previews.

The predictor receives only the image and inference settings. Annotations are used
after inference for scoring and visual comparisons, never as detector input.
"""

from __future__ import annotations

import csv
import html
import json
import math
import numbers
import os
from pathlib import Path
import statistics
import threading
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote

from .common import CLASS_NAMES, open_scan, read_crop, write_json

EDGES = ("xmin", "ymin", "xmax", "ymax")


def box_iou(first: Sequence[float], second: Sequence[float]) -> float:
    """Intersection over union for exclusive-maximum xyxy coordinates."""
    intersection = max(0.0, min(first[2], second[2]) - max(first[0], second[0])) * max(
        0.0, min(first[3], second[3]) - max(first[1], second[1])
    )
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    union = first_area + second_area - intersection
    return intersection / union if union > 0 else 0.0


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def document_metrics(
    truth: Mapping[str, Sequence[float]],
    predictions: Mapping[str, Sequence[float]],
    *,
    iou_threshold: float = 0.8,
) -> dict[str, Any]:
    """Score every required region, including absent predictions as zero IoU.

    Edge errors are undefined when a box is missing and are recorded as null;
    aggregate edge statistics explicitly expose their number of present boxes.
    """
    regions: dict[str, Any] = {}
    for class_name in CLASS_NAMES:
        expected = truth.get(class_name)
        predicted = predictions.get(class_name)
        present = expected is not None and predicted is not None
        regions[class_name] = {
            "iou": box_iou(expected, predicted) if present else 0.0,
            "missing": predicted is None,
            "annotation_missing": expected is None,
            "edge_absolute_error_px": {
                edge: abs(float(predicted[index]) - float(expected[index]))
                for index, edge in enumerate(EDGES)
            }
            if present
            else None,
        }
    return {
        "regions": regions,
        "all_regions_iou_ge_threshold": all(
            region["iou"] >= iou_threshold and not region["annotation_missing"]
            for region in regions.values()
        ),
    }


def aggregate_metrics(
    documents: Sequence[Mapping[str, Any]], *, iou_threshold: float = 0.8
) -> dict[str, Any]:
    """Aggregate document metrics without dropping failed or incomplete pages."""
    count = len(documents)
    classes: dict[str, Any] = {}
    for class_name in CLASS_NAMES:
        regions = [document["metrics"]["regions"][class_name] for document in documents]
        ious = [region["iou"] for region in regions]
        missing = sum(region["missing"] for region in regions)
        edge_errors = [region["edge_absolute_error_px"] for region in regions if region["edge_absolute_error_px"] is not None]
        classes[class_name] = {
            "documents": count,
            "present_predictions": count - missing,
            "missing_predictions": missing,
            "missing_rate": missing / count if count else None,
            "annotation_errors": sum(region["annotation_missing"] for region in regions),
            "iou_mean": statistics.fmean(ious) if ious else None,
            "iou_median": statistics.median(ious) if ious else None,
            "iou_p10": _percentile(ious, 10),
            "edge_error_count": len(edge_errors),
            "edge_absolute_error_px": {
                edge: {
                    "mean": statistics.fmean([errors[edge] for errors in edge_errors]) if edge_errors else None,
                    "median": statistics.median([errors[edge] for errors in edge_errors]) if edge_errors else None,
                    "p90": _percentile([errors[edge] for errors in edge_errors], 90),
                }
                for edge in EDGES
            },
        }
    passing = sum(document["metrics"]["all_regions_iou_ge_threshold"] for document in documents)
    runtimes = [document["runtime_seconds"] for document in documents]
    peak_values = [document["peak_process_rss_mb"] for document in documents if document.get("peak_process_rss_mb") is not None]
    return {
        "documents": count,
        "classes": classes,
        "all_regions_iou_ge_threshold": {
            "threshold": iou_threshold,
            "documents": passing,
            "fraction": passing / count if count else None,
        },
        "region_missing_rate": sum(classes[name]["missing_predictions"] for name in CLASS_NAMES) / (count * len(CLASS_NAMES)) if count else None,
        "documents_with_errors": sum(bool(document["errors"]) for document in documents),
        "runtime_seconds": {
            "total": sum(runtimes),
            "mean": statistics.fmean(runtimes) if runtimes else None,
            "median": statistics.median(runtimes) if runtimes else None,
            "p90": _percentile(runtimes, 90),
        },
        "peak_process_rss_mb_max": max(peak_values) if peak_values else None,
    }


class _MemorySampler:
    """Sample process RSS during each page; fallback is explicitly cumulative."""

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
                import sys

                raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                value = raw / (1024 * 1024) if sys.platform == "darwin" else raw / 1024
            else:
                return
            self.peak_mb = max(self.peak_mb or 0, value)
        except (OSError, ValueError, IndexError):
            return

    def _run(self) -> None:
        while not self._stop.wait(0.05):
            self._sample()

    def __enter__(self) -> "_MemorySampler":
        self._sample()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_args: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        self._sample()


def _font(size: int = 15) -> Any:
    from PIL import ImageFont

    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _draw_comparison(
    raster: Any,
    truth: Mapping[str, Sequence[float]],
    predictions: Mapping[str, Sequence[float]],
    *,
    region: Sequence[int],
    title: str,
    canvas_width: int = 900,
    before_shared_y: Mapping[str, Sequence[float]] | None = None,
) -> Any:
    from PIL import Image, ImageDraw

    header_height = 72
    canvas = Image.new("RGB", (max(canvas_width, raster.width), raster.height + header_height), "white")
    image_left = (canvas.width - raster.width) // 2
    canvas.paste(raster, (image_left, header_height))
    draw = ImageDraw.Draw(canvas)
    font = _font()
    draw.text((10, 8), title, fill="black", font=font)
    draw.text((10, 31), "Green: annotation    Magenta: prediction", fill="black", font=font)
    draw.text((10, 51), "D: depth  L: interpreted lithology  K: legend  Dashed blue/orange: before shared-Y", fill="black", font=_font(12))
    x0, y0, x1, y1 = region
    scale_x = raster.width / (x1 - x0)
    scale_y = raster.height / (y1 - y0)
    for boxes, color, prefix in ((truth, "#00984a", "GT"), (predictions, "#dc0085", "P")):
        for name, box in boxes.items():
            if name not in CLASS_NAMES:
                continue
            left = max(box[0], x0)
            top = max(box[1], y0)
            right = min(box[2], x1)
            bottom = min(box[3], y1)
            if right <= left or bottom <= top:
                continue
            coords = (
                image_left + (left - x0) * scale_x,
                header_height + (top - y0) * scale_y,
                image_left + (right - x0) * scale_x - 1,
                header_height + (bottom - y0) * scale_y - 1,
            )
            # A very narrow original column can be sub-pixel in the overview.
            coords = (coords[0], coords[1], max(coords[0], coords[2]), max(coords[1], coords[3]))
            draw.rectangle(coords, outline=color, width=2)
            label = f"{prefix} {dict(zip(CLASS_NAMES, ('D', 'L', 'K')))[name]}"
            label_y = max(header_height, coords[1] + (16 if prefix == "P" else 0))
            draw.text((coords[0] + 3, label_y), label, fill=color, font=_font(12), stroke_width=1, stroke_fill="white")
    for name, box in (before_shared_y or {}).items():
        if name not in CLASS_NAMES[:2]:
            continue
        left, top = max(box[0], x0), max(box[1], y0)
        right, bottom = min(box[2], x1), min(box[3], y1)
        if right <= left or bottom <= top:
            continue
        coords = (
            image_left + (left - x0) * scale_x,
            header_height + (top - y0) * scale_y,
            image_left + (right - x0) * scale_x - 1,
            header_height + (bottom - y0) * scale_y - 1,
        )
        _dashed_rectangle(draw, coords, fill={CLASS_NAMES[0]: "#0077cc", CLASS_NAMES[1]: "#e87500"}[name], width=2)
    return canvas


def save_comparisons(
    image_path: str | Path,
    truth: Mapping[str, Sequence[float]],
    predictions: Mapping[str, Sequence[float]],
    output_dir: str | Path,
    *,
    max_width: int = 1200,
    max_height: int = 1800,
    before_shared_y: Mapping[str, Sequence[float]] | None = None,
) -> list[str]:
    """Save an overview and informative crops without decoding a full RGB page.

    Each overview read is bounded to a square with the document's native width;
    the common reader downsizes that region before materializing RGB pixels.
    """
    from PIL import Image

    if max_width < 100 or max_height < 100:
        raise ValueError("Comparison bounds must be at least 100 pixels")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    with open_scan(image_path) as scan:
        width, height = scan.size
        scale = min(max_width / width, (max_height - 72) / height, 1.0)
        overview_width = max(1, round(width * scale))
        overview_height = max(1, round(height * scale))
        overview = Image.new("RGB", (overview_width, overview_height), "white")
        # Partition the output rows to avoid seams from rounded source positions.
        stripe_rows = max(1, min(256, round(width * scale)))
        for target_top in range(0, overview_height, stripe_rows):
            target_bottom = min(overview_height, target_top + stripe_rows)
            source_top = round(target_top * height / overview_height)
            source_bottom = round(target_bottom * height / overview_height)
            crop = read_crop(scan, (0, source_top, width, source_bottom), size=(overview_width, target_bottom - target_top))
            overview.paste(crop, (0, target_top))
            crop.close()
        composed = _draw_comparison(overview, truth, predictions, region=(0, 0, width, height), title="Full document comparison", canvas_width=min(900, max_width), before_shared_y=before_shared_y)
        target = output / "overview.jpg"
        composed.save(target, quality=90)
        paths.append(str(target))
        composed.close()
        overview.close()

        tile_height = min(width, height)
        column_boxes = [box for source in (truth, predictions) for name, box in source.items() if name in CLASS_NAMES[:2]]
        first_y = min((box[1] for box in column_boxes), default=0)
        last_y = max((box[3] for box in column_boxes), default=height)
        starts = {
            "header": 0,
            "column_top": round(first_y - tile_height * 0.25),
            "column_middle": round((first_y + last_y - tile_height) / 2),
            "column_bottom": round(last_y - tile_height * 0.75),
        }
        for label, desired_top in starts.items():
            top = max(0, min(height - tile_height, desired_top))
            crop_scale = min(max_width / width, (max_height - 72) / tile_height, 1.0)
            size = (max(1, round(width * crop_scale)), max(1, round(tile_height * crop_scale)))
            region = (0, top, width, top + tile_height)
            crop = read_crop(scan, region, size=size)
            composed = _draw_comparison(crop, truth, predictions, region=region, title=f"{label}: original y={top}..{top + tile_height}", canvas_width=min(900, max_width), before_shared_y=before_shared_y)
            target = output / f"{label}.jpg"
            composed.save(target, quality=92)
            paths.append(str(target))
            composed.close()
            crop.close()

        # Legends can lie several document widths below the first row, and can
        # themselves be taller than a square. Give them a separate comparison
        # that contains both complete boxes instead of relying on the header.
        legend_boxes = [source[CLASS_NAMES[2]] for source in (truth, predictions) if CLASS_NAMES[2] in source]
        if legend_boxes:
            left = min(box[0] for box in legend_boxes)
            top = min(box[1] for box in legend_boxes)
            right = max(box[2] for box in legend_boxes)
            bottom = max(box[3] for box in legend_boxes)
            pad_x = max(8, round((right - left) * 0.05))
            pad_y = max(8, round((bottom - top) * 0.05))
            legend_region = (
                max(0, math.floor(left) - pad_x),
                max(0, math.floor(top) - pad_y),
                min(width, math.ceil(right) + pad_x),
                min(height, math.ceil(bottom) + pad_y),
            )
            title = "Complete lithology legend comparison"
        else:
            legend_region = (0, 0, width, tile_height)
            title = "Lithology legend absent from annotation and prediction"
        region_width = legend_region[2] - legend_region[0]
        region_height = legend_region[3] - legend_region[1]
        legend_scale = min(max_width / region_width, (max_height - 72) / region_height, 1.0)
        legend_size = (max(1, round(region_width * legend_scale)), max(1, round(region_height * legend_scale)))
        crop = read_crop(scan, legend_region, size=legend_size)
        composed = _draw_comparison(crop, truth, predictions, region=legend_region, title=title, canvas_width=min(900, max_width), before_shared_y=before_shared_y)
        target = output / "legend.jpg"
        composed.save(target, quality=92)
        paths.append(str(target))
        composed.close()
        crop.close()
    return paths


_TRACE_COLORS = {
    CLASS_NAMES[0]: "#0077cc",
    CLASS_NAMES[1]: "#e87500",
    CLASS_NAMES[2]: "#7a3db8",
}
_TRACE_LABELS = dict(zip(CLASS_NAMES, ("D", "L", "K")))


def _dashed_rectangle(draw: Any, box: Sequence[float], *, fill: str, width: int = 2, dash: int = 8) -> None:
    left, top, right, bottom = box
    for start in range(round(left), round(right), dash * 2):
        draw.line((start, top, min(start + dash, right), top), fill=fill, width=width)
        draw.line((start, bottom, min(start + dash, right), bottom), fill=fill, width=width)
    for start in range(round(top), round(bottom), dash * 2):
        draw.line((left, start, left, min(start + dash, bottom)), fill=fill, width=width)
        draw.line((right, start, right, min(start + dash, bottom)), fill=fill, width=width)


def _trace_box_on_crop(box: Sequence[float], region: Sequence[int], size: tuple[int, int], header: int) -> tuple[float, float, float, float] | None:
    x0, y0, x1, y1 = region
    left, top = max(box[0], x0), max(box[1], y0)
    right, bottom = min(box[2], x1), min(box[3], y1)
    if right <= left or bottom <= top:
        return None
    scale_x, scale_y = size[0] / (x1 - x0), size[1] / (y1 - y0)
    return (
        (left - x0) * scale_x,
        header + (top - y0) * scale_y,
        max((left - x0) * scale_x, (right - x0) * scale_x - 1),
        max(header + (top - y0) * scale_y, header + (bottom - y0) * scale_y - 1),
    )


def _save_chunk_previews(
    image_path: str | Path,
    trace: Mapping[str, Any],
    output: Path,
    *,
    max_width: int = 1000,
) -> list[Path]:
    """Render every processed crop from a JSON-safe inference trace."""
    from PIL import Image, ImageDraw

    tiles = trace.get("tiles")
    detections = trace.get("detections")
    if not isinstance(tiles, list) or not isinstance(detections, list):
        return []
    by_id = {item.get("id"): item for item in detections if isinstance(item, Mapping)}
    pre_shared = trace.get("boxes_before_shared_y") if isinstance(trace.get("boxes_before_shared_y"), Mapping) else {}
    final_boxes = trace.get("final_boxes") if isinstance(trace.get("final_boxes"), Mapping) else {}
    chunk_dir = output / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    with open_scan(image_path) as scan:
        for tile in tiles:
            if not isinstance(tile, Mapping) or not isinstance(tile.get("box"), list) or len(tile["box"]) != 4:
                continue
            region = tuple(int(value) for value in tile["box"])
            source_width, source_height = region[2] - region[0], region[3] - region[1]
            scale = min(max_width / source_width, 1.0)
            size = (max(1, round(source_width * scale)), max(1, round(source_height * scale)))
            crop = read_crop(scan, region, size=size)
            header = 92
            canvas = Image.new("RGB", (crop.width, crop.height + header), "white")
            canvas.paste(crop, (0, header))
            crop.close()
            draw = ImageDraw.Draw(canvas)
            retry = bool(tile.get("retry"))
            tile_id = int(tile.get("tile_id", len(paths)))
            raw = [by_id[item_id] for item_id in tile.get("detection_ids", []) if item_id in by_id]
            accepted = sum(bool(item.get("selected")) for item in raw)
            draw.text((10, 8), f"chunk {tile_id:04d}  native y={region[1]}..{region[3]}  {'RETRY' if retry else 'scan'}", fill="#9a6500" if retry else "black", font=_font())
            draw.text((10, 32), f"raw detections={len(raw)}  selected={accepted}  rejected={len(raw) - accepted}", fill="black", font=_font(12))
            draw.text((10, 53), "Blue: depth  Orange: lithology  Purple: legend  Gray: rejected  Magenta: final", fill="black", font=_font(12))
            draw.text((10, 72), "Dashed: aggregate before shared-Y  Yellow frame: retry", fill="black", font=_font(12))
            if retry:
                draw.rectangle((1, header, canvas.width - 2, canvas.height - 2), outline="#f0b400", width=4)
            for item in raw:
                coords = _trace_box_on_crop(item.get("box", []), region, size, header)
                if coords is None:
                    continue
                class_name = str(item.get("class_name", "unknown"))
                color = _TRACE_COLORS.get(class_name, "#555555") if item.get("selected") else "#777777"
                draw.rectangle(coords, outline=color, width=3 if item.get("selected") else 2)
                label = f"{'A' if item.get('selected') else 'R'} {_TRACE_LABELS.get(class_name, '?')} {float(item.get('confidence', 0)):.2f}"
                draw.text((coords[0] + 3, max(header, coords[1] + 2)), label, fill=color, font=_font(11), stroke_width=1, stroke_fill="white")
            for name, box in pre_shared.items():
                if name not in CLASS_NAMES[:2]:
                    continue
                coords = _trace_box_on_crop(box, region, size, header)
                if coords is not None:
                    _dashed_rectangle(draw, coords, fill=_TRACE_COLORS[name], width=2)
            for name, box in final_boxes.items():
                coords = _trace_box_on_crop(box, region, size, header)
                if coords is not None:
                    draw.rectangle(coords, outline="#dc0085", width=2)
            target = chunk_dir / f"chunk_{tile_id:04d}.jpg"
            canvas.save(target, quality=88)
            canvas.close()
            paths.append(target)
    return paths


def _save_contact_sheet(chunk_paths: Sequence[Path], trace: Mapping[str, Any], target: Path) -> None:
    from PIL import Image, ImageDraw

    if not chunk_paths:
        return
    detections = {item.get("id"): item for item in trace.get("detections", []) if isinstance(item, Mapping)}
    final_boxes = trace.get("final_boxes") if isinstance(trace.get("final_boxes"), Mapping) else {}
    endpoints = [box[index] for name, box in final_boxes.items() if name in CLASS_NAMES[:2] for index in (1, 3)]

    def priority(tile: Mapping[str, Any]) -> tuple[int, int]:
        items = [detections[item_id] for item_id in tile.get("detection_ids", []) if item_id in detections]
        contains_endpoint = any(tile["box"][1] <= value <= tile["box"][3] for value in endpoints)
        rank = 0 if tile.get("retry") else 1 if any(not item.get("selected") for item in items) else 2 if contains_endpoint else 3
        return rank, int(tile.get("tile_id", 0))

    tiles = [item for item in trace.get("tiles", []) if isinstance(item, Mapping)]
    ordered_ids = [int(item.get("tile_id", 0)) for item in sorted(tiles, key=priority)[:24]]
    by_id = {int(path.stem.split("_")[-1]): path for path in chunk_paths}
    selected = [by_id[item_id] for item_id in ordered_ids if item_id in by_id]
    if not selected:
        return
    thumb_size = (260, 280)
    columns = 4
    rows = math.ceil(len(selected) / columns)
    header = 54
    sheet = Image.new("RGB", (columns * thumb_size[0], header + rows * thumb_size[1]), "white")
    draw = ImageDraw.Draw(sheet)
    draw.text((10, 8), "Priority chunks: retries, rejected detections, shared-Y endpoints, then scan order", fill="black", font=_font(14))
    draw.text((10, 31), "Open chunks/ for every processed crop.", fill="black", font=_font(12))
    for index, path in enumerate(selected):
        with Image.open(path) as image:
            thumbnail = image.copy()
        thumbnail.thumbnail((thumb_size[0] - 8, thumb_size[1] - 8))
        x = (index % columns) * thumb_size[0] + (thumb_size[0] - thumbnail.width) // 2
        y = header + (index // columns) * thumb_size[1] + (thumb_size[1] - thumbnail.height) // 2
        sheet.paste(thumbnail, (x, y))
        thumbnail.close()
    sheet.save(target, quality=88)
    sheet.close()


def save_prediction_review(
    image_path: str | Path,
    predictions: Mapping[str, Sequence[float]],
    output_dir: str | Path,
    *,
    truth: Mapping[str, Sequence[float]] | None = None,
    trace: Mapping[str, Any] | None = None,
    before_shared_y: Mapping[str, Sequence[float]] | None = None,
) -> list[str]:
    """Save final comparisons plus optional chunk-level inference evidence."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    trace_payload = dict(trace or {})
    original_boxes = before_shared_y or (
        trace_payload.get("boxes_before_shared_y")
        if isinstance(trace_payload.get("boxes_before_shared_y"), Mapping)
        else None
    )
    paths = save_comparisons(image_path, truth or {}, predictions, output, before_shared_y=original_boxes)
    chunk_paths: list[Path] = []
    contact_generated = False
    if trace_payload:
        inference_final = trace_payload.get("final_boxes")
        if inference_final != predictions:
            trace_payload["inference_final_boxes"] = inference_final
        trace_payload["final_boxes"] = {name: list(box) for name, box in predictions.items()}
        write_json(output / "trace.json", trace_payload)
        chunk_paths = _save_chunk_previews(image_path, trace_payload, output)
        contact = output / "chunks_contact_sheet.jpg"
        _save_contact_sheet(chunk_paths, trace_payload, contact)
        contact_generated = bool(chunk_paths) and contact.is_file()
    images = [Path(path).name for path in paths]
    if contact_generated:
        images.append("chunks_contact_sheet.jpg")
    chunk_links = sorted(chunk_paths)
    sections = "".join(f'<a href="{quote(name)}"><img src="{quote(name)}" alt="{html.escape(name)}"></a>' for name in images)
    chunks = "".join(f'<li><a href="chunks/{quote(path.name)}">{html.escape(path.name)}</a></li>' for path in chunk_links)
    unavailable = "" if trace_payload else "<p>Chunk trace unavailable: this review was generated from precomputed final boxes.</p>"
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(Path(image_path).name)}</title>
<style>body{{font-family:sans-serif;max-width:1200px;margin:auto;padding:20px}}img{{max-width:100%;max-height:700px;border:1px solid #ccc;margin:8px}}ul{{columns:4}}</style></head>
<body><h1>{html.escape(Path(image_path).name)}</h1>{unavailable}<div>{sections}</div><h2>All chunks</h2><ul>{chunks}</ul></body></html>"""
    (output / "index.html").write_text(page, encoding="utf-8")
    return paths


def write_visual_review_index(output_dir: str | Path, documents: Sequence[Mapping[str, Any]]) -> Path:
    """Create a worst-Y-first static index for an evaluation run."""
    output = Path(output_dir)

    def y_score(document: Mapping[str, Any]) -> float:
        values = []
        for name in CLASS_NAMES[:2]:
            region = document["metrics"]["regions"][name]
            errors = region.get("edge_absolute_error_px")
            if errors is None:
                return float("inf")
            values.extend((float(errors["ymin"]), float(errors["ymax"])))
        return max(values, default=0.0)

    cards = []
    for document in sorted(documents, key=y_score, reverse=True):
        stem = Path(document["image_name"]).stem
        score = y_score(document)
        score_label = "missing" if math.isinf(score) else f"{score:.1f}px"
        previews = document.get("comparison_previews", [])
        images = (
            f'<a href="{quote(stem)}/column_top.jpg"><img src="{quote(stem)}/column_top.jpg" alt="column top"></a>'
            f'<a href="{quote(stem)}/column_bottom.jpg"><img src="{quote(stem)}/column_bottom.jpg" alt="column bottom"></a>'
            if previews else "<p>Visual preview unavailable; inspect the document errors in report.json.</p>"
        )
        cards.append(
            f'<article><h2><a href="{quote(stem)}/index.html">{html.escape(document["image_name"])}</a></h2>'
            f'<p>Status: {html.escape(str(document["status"]))} · worst column Y error: {score_label}</p>'
            f'{images}</article>'
        )
    page = """<!doctype html><html><head><meta charset="utf-8"><title>Bounding-box visual review</title>
<style>body{font-family:sans-serif;max-width:1400px;margin:auto;padding:20px}article{border-top:2px solid #ddd;margin-top:24px}img{width:48%;max-height:550px;object-fit:contain;border:1px solid #ccc}</style></head>
<body><h1>Bounding-box visual review</h1><p>Documents are ordered by their worst depth/lithology Y-edge error.</p>""" + "".join(cards) + "</body></html>"
    target = output / "visual_review" / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(page, encoding="utf-8")
    return target


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def _before_shared_y_from_diagnostics(value: Any) -> Mapping[str, Sequence[float]] | None:
    pending = [value]
    seen: set[int] = set()
    while pending:
        item = pending.pop()
        if not isinstance(item, Mapping) or id(item) in seen:
            continue
        seen.add(id(item))
        shared = item.get("shared_column_vertical_extent")
        if isinstance(shared, Mapping) and isinstance(shared.get("original_boxes"), Mapping):
            return shared["original_boxes"]
        for key in ("yolo", "inference"):
            pending.append(item.get(key))
    return None


def _checked_boxes(
    payload: Mapping[str, Any], width: int, height: int, *, annotation: bool = False
) -> tuple[dict[str, list[float]], list[str]]:
    boxes = payload.get("bounding_boxes")
    if not isinstance(boxes, Mapping):
        raise ValueError("bounding_boxes must be an object")
    result: dict[str, list[float]] = {}
    errors: list[str] = []
    for name in CLASS_NAMES:
        box = boxes.get(name)
        if box is None:
            errors.append(f"Missing {'annotation' if annotation else 'prediction'}: {name}")
            continue
        if not isinstance(box, (list, tuple)) or len(box) != 4 or any(isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(value) for value in box):
            errors.append(f"Invalid coordinates for {name}: expected four finite numbers")
            continue
        left, top, right, bottom = box
        if not (0 <= left < right <= width and 0 <= top < bottom <= height):
            errors.append(f"Invalid coordinates for {name}: box must have positive area within {width}x{height}")
            continue
        result[name] = list(box)
    return result, errors


def _record_path(record: Mapping[str, Any], key: str, dataset_root: Path) -> Path:
    path = Path(record[key])
    return path if path.is_absolute() else dataset_root / path


def _write_csv(path: Path, documents: Sequence[Mapping[str, Any]]) -> None:
    fields = ["image_name", "group", "split", "status", "runtime_seconds", "processing_seconds", "peak_process_rss_mb", "memory_measurement", "all_regions_iou_ge_threshold", "errors"]
    fields += [f"{name}_{metric}" for name in CLASS_NAMES for metric in ("iou", "missing", *[f"{edge}_error_px" for edge in EDGES])]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for document in documents:
            row = {field: document.get(field) for field in fields if field in document}
            row["errors"] = " | ".join(document["errors"])
            row["all_regions_iou_ge_threshold"] = document["metrics"]["all_regions_iou_ge_threshold"]
            for name, region in document["metrics"]["regions"].items():
                row[f"{name}_iou"] = region["iou"]
                row[f"{name}_missing"] = region["missing"]
                for edge in EDGES:
                    row[f"{name}_{edge}_error_px"] = (region["edge_absolute_error_px"] or {}).get(edge)
            writer.writerow(row)


def evaluate_manifest(
    manifest_path: str | Path,
    weights: str | Path | None,
    output_dir: str | Path,
    *,
    split: str = "val",
    predictions_dir: str | Path | None = None,
    predictor: Callable[..., Mapping[str, Any]] | None = None,
    device: str | None = None,
    imgsz: int = 1536,
    conf: float = 0.25,
    iou_threshold: float = 0.8,
    save_previews: bool = True,
    debug_previews: bool = False,
) -> dict[str, Any]:
    """Evaluate final boxes on one manifest split and persist an auditable report.

    ``predictions_dir`` uses <image stem>.json files. An injected ``predictor``
    accepts the same image-only arguments as ``inference.predict_document``.
    Missing or corrupt files are document errors and remain in the denominator.
    """
    if split not in {"train", "val", "test"}:
        raise ValueError("split must be train, val, or test")
    if not 0 < iou_threshold <= 1:
        raise ValueError("iou_threshold must be in (0, 1]")
    if predictions_dir is not None and (weights is not None or predictor is not None):
        raise ValueError("Use either predictions_dir or a model/predictor")
    if predictions_dir is None and predictor is None:
        if weights is None:
            raise ValueError("Provide weights, predictions_dir, or an injected predictor")
        from .inference import UltralyticsDetector, predict_document

        detector = UltralyticsDetector(weights)

        def predictor(image_path: Path, **options: Any) -> Mapping[str, Any]:
            return predict_document(image_path, detector=detector, **options)
    manifest_path = Path(manifest_path).resolve()
    manifest = _read_json(manifest_path)
    dataset_root = Path(manifest.get("dataset_root", manifest_path.parent))
    if not dataset_root.is_absolute():
        dataset_root = manifest_path.parent / dataset_root
    records = [record for record in manifest["records"] if record.get("split") == split]
    if not records:
        raise ValueError(f"Manifest contains no documents for split {split!r}")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / "predictions").mkdir(exist_ok=True)
    documents: list[dict[str, Any]] = []
    for record in records:
        started = time.perf_counter()
        image_name = record["image_name"]
        stem = Path(image_name).stem
        errors: list[str] = []
        expected: dict[str, list[float]] = {}
        predicted: dict[str, list[float]] = {}
        payload: Mapping[str, Any] = {}
        previews: list[str] = []
        diagnostics: Any = None
        trace: dict[str, Any] = {}
        width, height = int(record["width"]), int(record["height"])
        image_path = _record_path(record, "image_path", dataset_root)
        with _MemorySampler() as memory:
            # Inference has no dependency on this annotation read or its contents.
            inference_started = time.perf_counter()
            try:
                if predictions_dir is not None:
                    prediction_path = Path(predictions_dir) / f"{stem}.json"
                    payload = _read_json(prediction_path)
                else:
                    assert predictor is not None
                    options = {"weights": weights, "device": device, "imgsz": imgsz, "confidence": conf}
                    if debug_previews:
                        options["trace"] = trace
                    payload = predictor(image_path, **options)
                if not isinstance(payload, Mapping):
                    raise ValueError("Predictor must return a JSON-like object")
                if payload.get("image_name", image_name) != image_name:
                    raise ValueError("Prediction image_name does not match manifest")
                if payload.get("width", width) != width or payload.get("height", height) != height:
                    raise ValueError("Prediction dimensions do not match manifest")
                predicted, box_errors = _checked_boxes(payload, width, height)
                errors.extend(box_errors)
                if payload.get("error"):
                    errors.append(str(payload["error"]))
                diagnostics = payload.get("diagnostics")
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
                partial_boxes = getattr(exc, "partial_boxes", {})
                if isinstance(partial_boxes, Mapping):
                    predicted, partial_errors = _checked_boxes({"bounding_boxes": partial_boxes}, width, height)
                    errors.extend(partial_errors)
                diagnostics = getattr(exc, "diagnostics", None)
            runtime = time.perf_counter() - inference_started
            try:
                annotation = _read_json(_record_path(record, "annotation_path", dataset_root))
                if annotation.get("image_name", image_name) != image_name:
                    raise ValueError("Annotation image_name does not match manifest")
                if annotation.get("width", width) != width or annotation.get("height", height) != height:
                    raise ValueError("Annotation dimensions do not match manifest")
                expected, annotation_errors = _checked_boxes(annotation, width, height, annotation=True)
                errors.extend(annotation_errors)
            except Exception as exc:
                errors.append(f"Annotation {type(exc).__name__}: {exc}")
            prediction_artifact: dict[str, Any] = {
                "image_name": image_name,
                "width": width,
                "height": height,
                "bounding_boxes": predicted,
            }
            if diagnostics is not None:
                prediction_artifact["diagnostics"] = diagnostics
            if errors:
                prediction_artifact["evaluation_errors"] = list(errors)
            write_json(output / "predictions" / f"{stem}.json", prediction_artifact)
            if save_previews:
                try:
                    previews = save_prediction_review(
                        image_path,
                        predicted,
                        output / "visual_review" / stem,
                        truth=expected,
                        trace=trace if debug_previews else None,
                        before_shared_y=_before_shared_y_from_diagnostics(diagnostics),
                    )
                except Exception as exc:
                    errors.append(f"Comparison preview {type(exc).__name__}: {exc}")
        document = {
            "image_name": image_name,
            "group": record.get("group"),
            "split": split,
            "status": "ok" if not errors else ("partial" if predicted else "error"),
            "errors": errors,
            "runtime_seconds": runtime,
            "processing_seconds": time.perf_counter() - started,
            "peak_process_rss_mb": memory.peak_mb,
            "memory_measurement": memory.method,
            "metrics": document_metrics(expected, predicted, iou_threshold=iou_threshold),
            "comparison_previews": [str(Path(path).relative_to(output)) for path in previews],
        }
        documents.append(document)
    if save_previews:
        write_visual_review_index(output, documents)
    report = {
        "manifest": str(manifest_path),
        "split": split,
        "prediction_source": "precomputed" if predictions_dir is not None else "model",
        "weights": str(weights) if weights is not None else None,
        "predictions_dir": str(Path(predictions_dir).resolve()) if predictions_dir is not None else None,
        "settings": {"imgsz": imgsz, "confidence": conf, "device": device, "iou_threshold": iou_threshold, "debug_previews": debug_previews},
        "metric_notes": {
            "coordinates": "Original image pixels; xyxy with exclusive maxima",
            "missing_predictions": "Zero IoU; included in every document and class denominator",
            "edge_errors": "Present annotation/prediction pairs only; see edge_error_count and missing_rate",
            "runtime_seconds": "Prediction or precomputed JSON loading only; one-time model loading is excluded. processing_seconds includes comparisons and scoring",
            "memory": "Process RSS, including model and previews; sampled every 50 ms on Linux, so short peaks may be missed. Fallback process_lifetime_peak_rss is cumulative, not a per-document allocation peak. GPU memory is not included.",
        },
        "summary": aggregate_metrics(documents, iou_threshold=iou_threshold),
        "documents": documents,
    }
    write_json(output / "report.json", report)
    _write_csv(output / "per_document.csv", documents)
    return report
