"""
hybrid_pipeline.py: method "hybrid" of lithology_pipeline.py, the best parts of both methods, fast path first.

    column      cv_pipeline   frame/track stripping, pattern period, grid-line removal, k-means clustering of window
                              textures, transition guard, boundary snapping to the exact row          (1-2 px boundaries)
    legend      1. cv_pipeline size-group finder (Tesseract, ~seconds)
                2. if it looks wrong (few swatches / glued names): lithology/extract.py parse_legend (RapidOCR, robust
                   on mud logs whose swatches are wide strips with the name on the right)
                3. if that is still poor: cv_pipeline text-anchored finder (Tesseract)
    naming      cv_pipeline: every *cluster* (not every window) is matched to the legend with scale-invariant features
                against resized x0.35-1.5 + thickened 0-3 px copies of each swatch (the column is often drawn at another
                size / stroke weight than the legend); identical swatches (Shale = Clay) resolved by a commonness prior
                and flagged ambiguous, near-ties flagged too
    depth       1. cv_pipeline Tesseract reader (sideways labels, then upright "3,000" labels), ~seconds
                2. if < min inliers: lithology/extract.py calibrate_depth (RapidOCR, any orientation, piecewise fit) on a
                   few chunks first, more chunks only if still needed
    output      neighbours with the same name merged; CSV, painted PNGs, legend/depth plots, notebook-style TIF
"""
from __future__ import annotations

import contextlib
import io
import json
import time
import tracemalloc
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

import sys
_REPO = str(Path(__file__).resolve().parents[2])                    # the repository root holds lithology/
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from . import cv_pipeline as cv                                     # noqa: E402
from lithology import drawing as LD                                 # noqa: E402
from lithology import extract as LX  # noqa: E402

ProgressFn = Callable[[dict], None]


def _quiet():
    return contextlib.redirect_stderr(io.StringIO())


# ----------------------------------------------------------------------------------------------------------------------
# Legend
# ----------------------------------------------------------------------------------------------------------------------
def _name(label: str) -> str:
    """'SHALE' / 'Sand and Sandstone' -> 'Shale' / 'Sand And Sandstone' (the commonness prior and the CSV use these)."""
    return " ".join(w.capitalize() for w in label.split())


def swatches_from_rapidocr(image, leg_box, leg_ink, leg_rgb) -> list:
    """lithology/extract.py parse_legend -> cv Swatch objects (inner pattern as an ink mask, + colour)."""
    with _quiet():
        entries = LX.parse_legend(image, leg_box)
    lx0, ly0 = leg_box[0], leg_box[1]
    out, seen = [], set()
    for e in entries:
        name = _name(e["label"])                                    # already fuzzy-corrected by extract.clean_label
        if not name or name in seen or name.lower() in ("and", "unknown"):
            continue
        x0, y0, x1, y1 = LX.shrink([e["swatch_bbox"][0] - lx0, e["swatch_bbox"][1] - ly0,
                                    e["swatch_bbox"][2] - lx0, e["swatch_bbox"][3] - ly0], frac=0.12, min_px=1)
        ink = leg_ink[y0:y1, x0:x1]
        rgb = leg_rgb[y0:y1, x0:x1] if leg_rgb is not None else None
        if ink.size == 0 or (ink.mean() <= 0.005 and rgb is None):
            continue                                                # empty box / symbol only
        seen.add(name)
        out.append(cv.Swatch(name=name, box=(int(x0), int(y0), int(x1 - x0), int(y1 - y0)), ink=ink, rgb=rgb))
    return out


def read_legend(image, boxes, cfg, ccfg) -> tuple:
    """Fast Tesseract finder first; RapidOCR (Pau) when it looks wrong; text-anchored Tesseract finder last."""
    leg_box = boxes["lithology_legend"]
    leg_crop = image.crop(tuple(leg_box))
    leg_ink = cv.to_ink(leg_crop, ccfg)
    leg_rgb = np.array(leg_crop.convert("RGB")) if cv.is_colour(leg_crop) else None
    tried = []
    order = ["rapidocr", "size-groups", "text-anchored"] if cfg.hybrid_prefer_rapidocr else \
        ["size-groups", "rapidocr", "text-anchored"]
    best, best_method = [], "none"
    for method in order:
        try:
            if method == "size-groups":
                sw = cv.find_swatches(leg_ink, leg_rgb)
            elif method == "rapidocr":
                sw = swatches_from_rapidocr(image, leg_box, leg_ink, leg_rgb)
            else:
                sw = cv.find_swatches_by_text(leg_ink, leg_rgb)
        except Exception as e:                                      # noqa: BLE001  (e.g. OCR engine missing)
            tried.append(f"{method}: {type(e).__name__}")
            continue
        tried.append(f"{method}: {len(sw)}")
        good = [s for s in sw if len(s.name.split()) <= 4]
        if len(good) > len(best):
            best, best_method = good, method
        if not cv.swatches_look_wrong(sw):
            break
    return best, best_method, tried


# ----------------------------------------------------------------------------------------------------------------------
# Depth
# ----------------------------------------------------------------------------------------------------------------------
def read_depth(image, box, cfg, ccfg) -> tuple:
    """Returns (y -> depth function or None, info dict or None, how). Tesseract first; RapidOCR when it is weak."""
    fit, how = None, []
    try:
        fit = cv.calibrate_depth(image, box, ccfg)
        how.append(f"tesseract: {fit.n_used if fit else 0}")
    except Exception as e:                                          # noqa: BLE001
        how.append(f"tesseract: {type(e).__name__}")
    if fit is not None and fit.n_used >= cfg.hybrid_depth_min_labels:
        info = dict(a=fit.a, b=fit.b, n_used=fit.n_used, n_read=fit.n_read, model="linear", rms_px=None,
                    points=fit.points, engine="tesseract")
        return (lambda y: fit.a * y + fit.b), info, how
    best = None
    for n in sorted({min(8, cfg.depth_max_chunks), cfg.depth_max_chunks}):   # a few chunks first, more if needed
        try:
            with _quiet():
                f, calib = LX.calibrate_depth(image, box, chunk=cfg.depth_chunk, max_chunks=n)
            how.append(f"rapidocr x{n}: {calib['n_labels_used']}")
            if best is None or calib["n_labels_used"] > best[1]["n_labels_used"]:
                best = (f, calib)
            if calib["n_labels_used"] >= max(cfg.hybrid_depth_min_labels, 6):
                break
        except Exception as e:                                      # noqa: BLE001
            how.append(f"rapidocr x{n}: {type(e).__name__}")
    if best is not None and (fit is None or best[1]["n_labels_used"] > fit.n_used):
        f, c = best
        info = dict(a=c["depth_per_px"], b=c["intercept"], n_used=c["n_labels_used"],
                    n_read=c["n_labels_used"] + c["n_labels_rejected"], model=c["model"],
                    rms_px=round(c["rms_residual_px"], 2), points=c["labels"], engine="rapidocr")
        return (lambda y: float(f(y))), info, how
    if fit is not None:
        info = dict(a=fit.a, b=fit.b, n_used=fit.n_used, n_read=fit.n_read, model="linear", rms_px=None,
                    points=fit.points, engine="tesseract")
        return (lambda y: fit.a * y + fit.b), info, how
    return None, None, how


# ----------------------------------------------------------------------------------------------------------------------
# One log
# ----------------------------------------------------------------------------------------------------------------------
def process_log_hybrid(log, out_dir: Path, cfg, ccfg, colours, on_progress: ProgressFn) -> dict:
    """cfg = lithology_pipeline.Config (all settings), ccfg = the matching cv_pipeline.Config."""
    timings, t0 = {}, time.perf_counter()
    tracemalloc.start()

    def lap(name):
        nonlocal t0
        timings[name] = round(time.perf_counter() - t0, 3)
        on_progress(dict(event="stage", log=log.stem, stage=name, seconds=timings[name]))
        t0 = time.perf_counter()

    ann = json.loads(Path(log.annotation_path).read_text())
    boxes = ann["bounding_boxes"]
    image = LX.load_page(Path(log.image_path), ann)

    # 1-3: column, scale (cv)
    ink, rgb, col_x0, col_y0 = cv.prepare_column(image, boxes["interpreted_lithology_column"], ccfg)
    scale = cv.measure_period(ink, ccfg)
    ink = cv.remove_grid_lines(ink, scale, ccfg)
    describe = cv.TextureDescriber(scale)
    lap("column")

    # 4-5: features, clusters, beds (cv)
    F, centres, dens = cv.window_features(ink, rgb, scale, describe, ccfg)
    labels, X, centroids, silhouette = cv.cluster_windows(F, ccfg)
    labels, transitions = cv.transition_guard(labels, X, centroids, scale)
    beds = cv.refine_boundaries(ink, cv.labels_to_beds(labels, centres, ink.shape[0], scale), scale, describe)
    lap("segmentation")

    # 6-7: legend (fast first, RapidOCR fallback) + naming (cv variants + look-alikes)
    swatches, legend_method, legend_tried = read_legend(image, boxes, cfg, ccfg)
    groups = cv.lookalike_groups(swatches, describe)
    rock = {int(c) for c in np.unique(labels) if np.median(dens[labels == c]) >= ccfg.blank_density}
    cluster_feats = {c: np.median(F[labels == c], 0) for c in rock}
    matches = cv.name_clusters(cluster_feats, swatches, F, ink.shape[1], scale, describe, groups, ccfg)
    lap("legend+naming")

    # 8: depth (Tesseract first, RapidOCR fallback)
    y_to_depth, depth_info, depth_how = (None, None, ["no depth_column box"])
    if "depth_column" in boxes:
        y_to_depth, depth_info, depth_how = read_depth(image, boxes["depth_column"], cfg, ccfg)
    lap("depth")

    def depth(y):
        if y_to_depth is None:
            return None
        v = float(y_to_depth(y))
        return round(float(np.round(v / cfg.round_depth) * cfg.round_depth), 4) if cfg.round_depth else round(v, 2)

    intervals = []
    for c, top, bottom in beds:
        if c not in matches:
            continue                                                # blank paper
        m = matches[c]
        ty, by = int(top + col_y0), int(bottom + col_y0)
        r = dict(top_y=ty, bottom_y=by, cluster_id=int(c), lithology=m.name, top_depth=depth(ty), bottom_depth=depth(by),
                 ambiguous=bool(m.ambiguous))
        p = intervals[-1] if intervals else None
        if cfg.merge_same_name and p and p["lithology"] == r["lithology"] != "Unknown" and p["bottom_y"] == r["top_y"]:
            p.update(bottom_y=by, bottom_depth=r["bottom_depth"], ambiguous=p["ambiguous"] or r["ambiguous"])
        else:
            intervals.append(r)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # outputs
    out_dir = Path(out_dir)
    csv_path = out_dir / f"{log.stem}.csv"
    pd.DataFrame(intervals, columns=["top_depth", "bottom_depth", "lithology", "top_y", "bottom_y", "cluster_id"]) \
        .to_csv(csv_path, index=False)
    page = out_dir / f"{log.stem}_page.png"
    cv.render(image, boxes, intervals, colours, scale=cfg.page_scale).save(page)
    cb = boxes["interpreted_lithology_column"]
    db = boxes.get("depth_column", cb)
    region = (0, cb[1], image.width, cb[3]) if cfg.column_full_width else \
        (max(0, min(cb[0], db[0]) - 150), cb[1], min(image.width, max(cb[2], db[2]) + 260), cb[3])
    column_png = out_dir / f"{log.stem}_column.png"
    cv.render(image, {k: v for k, v in boxes.items() if k != "lithology_legend"}, intervals, colours, region,
              cfg.column_scale).save(column_png)
    legend_png = cv.plot_legend(swatches, groups, out_dir / f"{log.stem}_legend.png")
    fit = None if depth_info is None else cv.DepthFit(depth_info["a"], depth_info["b"], depth_info["n_used"],
                                                      depth_info["n_read"], depth_info["points"])
    depth_png = cv.plot_depth(fit, out_dir / f"{log.stem}_depth.png")
    tif_path, tif_error = None, None
    if cfg.annotated_tif and intervals and depth_info and "depth_column" in boxes:
        try:
            tif_out = out_dir / f"{log.stem}_annotated.tif"
            tif_layout = LD.render_tif(image, log.annotation_path, dict(intervals=intervals), tif_out)  # returns its layout
            tif_path = str(tif_out)
        except Exception as e:                                      # noqa: BLE001
            tif_error = f"{type(e).__name__}: {e}"
    lap("render")

    record = dict(
        stem=log.stem, image=str(log.image_path), method="hybrid", colour_scan=rgb is not None, scale=asdict(scale),
        k=int(len(np.unique(labels))), silhouette=round(silhouette, 3), transition_clusters_removed=transitions,
        n_intervals=len(intervals), legend_method=f"{legend_method} (tried {', '.join(legend_tried)})",
        swatches=[dict(name=s.name, look_alikes=[swatches[j].name for j in groups[i] if j != i]) for i, s in enumerate(swatches)],
        clusters={str(c): asdict(m) for c, m in matches.items()},
        ambiguous=sorted({r["lithology"] for r in intervals if r["ambiguous"]}),
        depth=None if depth_info is None else {k: v for k, v in depth_info.items() if k != "points"},
        depth_error=None if depth_info else "no depth: " + "; ".join(depth_how), depth_tried=depth_how,
        intervals=intervals, timings_s=timings, runtime_s=round(sum(timings.values()) - timings["render"], 3),
        peak_mb=round(peak / 1e6, 1),
        files=dict(csv=str(csv_path), page_png=str(page), column_png=str(column_png), legend_png=legend_png,
                   depth_png=depth_png, annotated_tif=tif_path),
        annotated_tif_error=tif_error,
    )
    if log.labels_path:
        record["metrics"] = cv.evaluate(intervals, pd.read_csv(log.labels_path).to_dict("records"))
    return record
