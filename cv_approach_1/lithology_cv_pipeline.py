"""
lithology_cv_pipeline.py: method "lithology_cv" of lithology_pipeline.py (the default) = hybrid_v3 + Pau's newest
lithology/ package (main, Nov 8: legend extraction + grid search).

What it keeps from hybrid_v3 (hybrid_v3.py / hybrid_v2.py):
    column      ink masks, pattern period, grid-line removal, texture features, colour mode (Lab fill colour per
                row against legend colour groups), blank fix, transition guard, boundary refinement
    depth       Tesseract in 3000-row strips with an orientation probe, Pau's robust fit (RANSAC + MAD refit +
                piecewise), RapidOCR fallback; read in a background thread while the column is analysed
    speed       OMP_THREAD_LIMIT=1 for Tesseract, PIL legend plot, annotated TIF drawn on demand

What is new (Pau's lithology/legend_grid.py):
    legend      "grid" parser: swatch columns -> non-overlapping cells, one OCR per column strip, so a label can
                never drift into its neighbour's cell, swatches hidden by OCR are restored, labels below swatches
                are handled. It joins the legend readers that compete on name quality:
                    1. size-groups    (Tesseract, fast)   } good enough (>= 3 swatches, quality >= 80 %) -> stop
                    2. text-anchored  (Tesseract)         }
                    3. grid           (Pau, RapidOCR)     <- new
                    4. parse_legend   (Pau's old RapidOCR parser) only if grid found nothing
                and the best-named set wins (hybrid_v2.name_quality). With hybrid_prefer_rapidocr, grid goes first.
    labels      grid's clean_label keeps "Clay-M" / "Sand-SS" style codes; our tidy_name then fixes casing/joins.

Works before the merge too: without lithology/legend_grid.py the grid reader is skipped (reported in the record).
"""
from __future__ import annotations

import numpy as np

import cv_pipeline as cv
import hybrid_pipeline as hy                                     # noqa: F401  (sets sys.path for lithology/)
import hybrid_v2 as h2
import hybrid_v3 as h3
from lithology import extract as LX

try:
    from lithology.legend_grid import parse_legend_grid
except ImportError:                                              # lithology/ older than main of Nov 8
    parse_legend_grid = None


def swatches_from_entries(entries, leg_box, leg_ink, leg_rgb) -> list:
    """lithology/ legend entries (any of Pau's parsers) -> cv Swatch objects (inner pattern as ink mask + colour)."""
    lx0, ly0 = leg_box[0], leg_box[1]
    out, seen = [], set()
    for e in entries:
        name = hy._name(e["label"])
        if not name or name in seen or name.lower() in ("and", "unknown"):
            continue
        x0, y0, x1, y1 = LX.shrink([e["swatch_bbox"][0] - lx0, e["swatch_bbox"][1] - ly0,
                                    e["swatch_bbox"][2] - lx0, e["swatch_bbox"][3] - ly0], frac=0.12, min_px=1)
        x0, y0 = max(0, int(x0)), max(0, int(y0))
        ink = leg_ink[y0:int(y1), x0:int(x1)]
        rgb = leg_rgb[y0:int(y1), x0:int(x1)] if leg_rgb is not None else None
        if ink.size == 0 or (ink.mean() <= 0.005 and rgb is None):
            continue                                             # empty box / symbol only
        seen.add(name)
        out.append(cv.Swatch(name=name, box=(x0, y0, int(x1) - x0, int(y1) - y0), ink=ink, rgb=rgb))
    return out


def read_legend_lcv(image, boxes, cfg, ccfg) -> tuple:
    """Legend readers in turn (see the module docstring); the best-named set of swatches wins."""
    leg_box = boxes["lithology_legend"]
    leg_crop = image.crop(tuple(leg_box))
    leg_ink = cv.to_ink(leg_crop, ccfg)
    leg_rgb = np.array(leg_crop.convert("RGB")) if cv.is_colour(leg_crop) else None
    sources = ["grid", "size-groups", "text-anchored", "rapidocr"] if cfg.hybrid_prefer_rapidocr else \
        ["size-groups", "text-anchored", "grid", "rapidocr"]
    results, tried = [], []
    for src in sources:
        if src == "rapidocr" and any(r[1] == "grid" and r[2] for r in results):
            continue                                             # grid supersedes Pau's old parser
        try:
            if src == "size-groups":
                sw = cv.find_swatches(leg_ink, leg_rgb)
            elif src == "text-anchored":
                sw = cv.find_swatches_by_text(leg_ink, leg_rgb)
            elif src == "grid":
                if parse_legend_grid is None:
                    tried.append("grid: not available (merge main for lithology/legend_grid.py)")
                    continue
                with hy._quiet():
                    sw = swatches_from_entries(parse_legend_grid(image, leg_box), leg_box, leg_ink, leg_rgb)
            else:
                sw = hy.swatches_from_rapidocr(image, leg_box, leg_ink, leg_rgb)
        except Exception as e:                                   # noqa: BLE001  (e.g. OCR engine missing)
            tried.append(f"{src}: {type(e).__name__}")
            continue
        sw = h2.normalise_strip_swatches(h2._retidy(sw), leg_ink, leg_rgb)
        q = h2.name_quality(sw)
        tried.append(f"{src}: {len(sw)} (quality {q:.0f})")
        results.append((q, src, sw))
        if len(sw) >= 3 and q >= 0.8 * len(sw):                  # good enough: skip the slower readers
            break
    if not results:
        return [], "none", tried
    q, src, sw = max(results, key=lambda r: r[0])
    return sw, src, tried


def process_log_lcv(log, out_dir, cfg, ccfg, colours, on_progress) -> dict:
    return h2.process_log_v2(log, out_dir, cfg, ccfg, colours, on_progress, depth_reader=h3.read_depth_v3,
                             method_name="lithology_cv", parallel_depth=True, legend_plot=h3.plot_legend_fast,
                             legend_reader=read_legend_lcv)
