"""
lithology_cv_pipeline.py: methods "lithology_cv" and "automatic_lithology_cv" (the default) of lithology_pipeline.py.

lithology_cv = hybrid_v3 + Pau's grid legend reader (lithology/legend_grid.py), tuned for accuracy, not speed.

What it keeps from hybrid_v3 (hybrid_v3.py / hybrid_v2.py):
    column      ink masks, pattern period, grid-line removal, texture features, colour mode (Lab fill colour per
                row against legend colour groups), blank fix, transition guard, boundary refinement
    depth       Tesseract in 3000-row strips with an orientation probe, Pau's robust fit (RANSAC + MAD refit +
                piecewise), RapidOCR fallback; read in a background thread while the column is analysed

Legend: every reader runs, the best-named result wins (hybrid_v2.name_quality):
    size-groups    Tesseract: boxes of the same size, the text next to them
    text-anchored  Tesseract: the words first, then the box beside each word
    grid           Pau (RapidOCR): swatch columns -> non-overlapping cells, one OCR per column strip, so a label never
                   drifts into its neighbour's cell; swatches hidden by OCR are restored; labels below swatches handled
    parse_legend   Pau's older RapidOCR reader, only when grid finds nothing
On a tie the earlier reader wins (the Tesseract readers are the ones validated on the labelled logs).
When grid wins, its swatches keep grid's names but borrow the pattern boxes of the Tesseract readers at the same
place (grid cuts swatches tightly: on Callaway, Anhydrite's diagonal lines fell outside and the column matched the
wrong rock). Checked on Callaway: 99.6 % of rows as before, with grid's better names and 3 more legend entries.

automatic_lithology_cv = lithology_cv + "Automatic bounding boxes" (Config.automatic_bounding_boxes, on by default):
    1. the detector (auto_boxes.py, bbox-final int8 models) finds the three boxes on the scan, even when a .json was
       uploaded;
    2. if it fails (not installed, error, a required box missing), the uploaded .json is used instead;
    3. with no .json either, the log fails with the detector's reason.
    A depth column the detector missed is taken from the .json when there is one. When both exist, the overlap (IoU)
    of every detected box with the .json's box is reported. The boxes used are saved as <stem>.boxes.json.
"""
from __future__ import annotations

import dataclasses
import json
import time
from pathlib import Path

import numpy as np

from . import auto_boxes
from . import cv_pipeline as cv
from . import hybrid_pipeline as hy                              # also puts the repo root on sys.path for lithology/
from . import hybrid_v2 as h2
from . import hybrid_v3 as h3
from lithology import extract as LX

try:
    from lithology.legend_grid import parse_legend_grid
except ImportError:                                              # lithology/ older than main of Nov 8
    parse_legend_grid = None


# ----------------------------------------------------------------------------------------------------------------------
# Legend
# ----------------------------------------------------------------------------------------------------------------------
def swatches_from_entries(entries, leg_box, leg_ink, leg_rgb) -> list:
    """lithology/ legend entries (any of Pau's parsers) -> cv Swatch objects (inner pattern as ink mask + colour)."""
    lx0, ly0 = leg_box[0], leg_box[1]
    H, W = leg_ink.shape
    out, seen = [], set()
    for e in entries:
        name = hy._name(e["label"])
        if not name or name in seen or name.lower() in ("and", "unknown"):
            continue
        x0, y0, x1, y1 = LX.shrink([e["swatch_bbox"][0] - lx0, e["swatch_bbox"][1] - ly0,
                                    e["swatch_bbox"][2] - lx0, e["swatch_bbox"][3] - ly0], frac=0.04, min_px=1)
        x0, y0, x1, y1 = max(0, int(x0)), max(0, int(y0)), min(W, int(x1)), min(H, int(y1))
        ink = leg_ink[y0:y1, x0:x1]
        rgb = leg_rgb[y0:y1, x0:x1] if leg_rgb is not None else None
        if ink.size == 0 or (ink.mean() <= 0.005 and rgb is None):
            continue                                             # empty box / symbol only
        seen.add(name)
        out.append(cv.Swatch(name=name, box=(x0, y0, x1 - x0, y1 - y0), ink=ink, rgb=rgb))
    return out


def read_legend_lcv(image, boxes, cfg, ccfg) -> tuple:
    """Every legend reader runs (see the module docstring); the best-named set of swatches wins."""
    leg_box = boxes["lithology_legend"]
    leg_crop = image.crop(tuple(leg_box))
    leg_ink = cv.to_ink(leg_crop, ccfg)
    leg_rgb = np.array(leg_crop.convert("RGB")) if cv.is_colour(leg_crop) else None
    results, tried, reader_timings = [], [], {}
    for src in ("size-groups", "text-anchored", "grid", "rapidocr"):
        if src == "rapidocr" and any(r[1] == "grid" and r[2] for r in results):
            continue                                             # grid supersedes Pau's older reader
        reader_started = time.perf_counter()
        try:
            if src == "size-groups":
                sw = cv.find_swatches(leg_ink, leg_rgb)
            elif src == "text-anchored":
                sw = cv.find_swatches_by_text(leg_ink, leg_rgb)
            elif src == "grid":
                if parse_legend_grid is None:
                    tried.append("grid: not available (lithology/legend_grid.py missing)")
                    continue
                with hy._quiet():
                    sw = swatches_from_entries(parse_legend_grid(image, leg_box), leg_box, leg_ink, leg_rgb)
            else:
                sw = hy.swatches_from_rapidocr(image, leg_box, leg_ink, leg_rgb)
        except Exception as e:                                   # noqa: BLE001  (e.g. OCR engine missing)
            reader_timings[src] = round(time.perf_counter() - reader_started, 3)
            tried.append(f"{src}: {type(e).__name__}: {e}"[:160])
            continue
        reader_timings[src] = round(time.perf_counter() - reader_started, 3)
        sw = h2.normalise_strip_swatches(h2._retidy(sw), leg_ink, leg_rgb)
        q = h2.name_quality(sw)
        tried.append(f"{src}: {len(sw)} (quality {q:.1f})")
        results.append((q, src, sw))
    if not results:
        return [], "none", tried, reader_timings
    q, src, sw = max(results, key=lambda r: r[0])                # max() keeps the first of equal scores
    if src == "grid":
        n = borrow_boxes(sw, [s for r in results if r[1] in ("size-groups", "text-anchored") for s in r[2]])
        tried.append(f"grid names + {n} swatch boxes from the Tesseract readers")
    return sw, src, tried, reader_timings


def _box_iou(a, b) -> float:
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    ix = max(0, min(ax0 + aw, bx0 + bw) - max(ax0, bx0))
    iy = max(0, min(ay0 + ah, by0 + bh) - max(ay0, by0))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def borrow_boxes(grid_sw, other_sw) -> int:
    """Grid reads names best but cuts its swatches tightly (thin diagonal lines can fall outside: Callaway's
    Anhydrite had no ink left, so the column matched Shaly Siltstone). Each grid swatch keeps its name and takes the
    pattern box of the Tesseract readers' swatch at the same place (IoU > 0.3). Returns how many were replaced."""
    n = 0
    for s in grid_sw:
        best = max(other_sw, key=lambda o: _box_iou(s.box, o.box), default=None)
        if best is not None and _box_iou(s.box, best.box) > 0.3:
            s.box, s.ink, s.rgb = best.box, best.ink, best.rgb
            n += 1
    return n


def process_log_lcv(log, out_dir, cfg, ccfg, colours, on_progress, method_name: str = "lithology_cv") -> dict:
    return h2.process_log_v2(log, out_dir, cfg, ccfg, colours, on_progress, depth_reader=h3.read_depth_v3,
                             method_name=method_name, parallel_depth=True, legend_plot=h3.plot_legend_fast,
                             legend_reader=read_legend_lcv)


# ----------------------------------------------------------------------------------------------------------------------
# Automatic bounding boxes
# ----------------------------------------------------------------------------------------------------------------------
def resolve_boxes(log, out_dir: Path, cfg) -> tuple:
    """(annotation path to use, info dict). See the module docstring for the order."""
    json_p = Path(log.annotation_path) if log.annotation_path and Path(log.annotation_path).is_file() else None
    info = dict(boxes_source="json", boxes_note="", boxes_iou=None)
    if not cfg.automatic_bounding_boxes:
        if json_p is None:
            raise RuntimeError("no .json annotation and 'automatic_bounding_boxes' is off")
        info["boxes_note"] = "automatic bounding boxes off: boxes from the uploaded .json"
        return json_p, info
    out_p = Path(out_dir) / f"{log.stem}.boxes.json"
    try:
        ann = auto_boxes.detect(log.image_path, out_p, json_hint=json_p)
    except Exception as e:                                       # noqa: BLE001  any failure -> the .json
        if json_p is None:
            raise RuntimeError(f"automatic bounding boxes failed ({e}) and no .json was uploaded") from None
        info["boxes_note"] = f"automatic bounding boxes failed ({e}); used the uploaded .json"
        return json_p, info
    info["boxes_source"] = "automatic"
    info["boxes_note"] = "boxes found automatically"
    if json_p is not None:
        try:
            given = json.loads(json_p.read_text()).get("bounding_boxes") or {}
        except (OSError, json.JSONDecodeError):
            given = {}
        if "depth_column" not in ann["bounding_boxes"] and "depth_column" in given:
            ann["bounding_boxes"]["depth_column"] = given["depth_column"]
            out_p.write_text(json.dumps(ann, indent=2))
            info["boxes_note"] += "; depth column from the .json (not detected)"
        info["boxes_iou"] = {k: round(auto_boxes.iou(ann["bounding_boxes"][k], given[k]), 3)
                             for k in auto_boxes.BOXES if k in ann["bounding_boxes"] and k in given}
    return out_p, info


def process_log_auto(log, out_dir, cfg, ccfg, colours, on_progress) -> dict:
    t0 = time.perf_counter()
    ann_p, info = resolve_boxes(log, out_dir, cfg)
    t_boxes = round(time.perf_counter() - t0, 3)
    on_progress(dict(event="stage", log=log.stem, stage="boxes", seconds=t_boxes, note=info["boxes_note"]))
    record = process_log_lcv(dataclasses.replace(log, annotation_path=ann_p), out_dir, cfg, ccfg, colours, on_progress,
                             method_name="automatic_lithology_cv")
    record["timings_s"] = {"boxes": t_boxes, **record.get("timings_s", {})}
    record["runtime_s"] = round(record.get("runtime_s", 0) + t_boxes, 3)
    record.update(info, annotation=str(ann_p))
    return record
