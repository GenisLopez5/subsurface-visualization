"""
hybrid_v3.py: method "hybrid_v3" of lithology_pipeline.py = hybrid_v2 (colour mode, blank fix, legend quality score,
strip-legend box normalisation, cluster path for black-and-white logs) + Pau's improvements from lithology/ (merged
from main):

    depth     Pau's robust depth fit (lithology/extract.py) applied to *every* set of depth labels, also the fast
              Tesseract ones: RANSAC over label pairs with a tolerance from the label spacing, then a second pass
              that drops labels far from the line (MAD), refit, and a piecewise-linear model when one line does not
              explain the labels (scale change / depth break). The fit error (rms, px) is reported. Tesseract is still
              read first (seconds); RapidOCR (lithology/extract.py, any orientation) is the fallback when Tesseract
              finds too few labels or fits them badly.
    drawing   Pau's new lithology/drawing.render_tif: full resolution, drawn in chunks (low memory on huge scans),
              Deflate-compressed; its layout (colour-key bar height, scale, y offset) is kept in the record so a UI can
              place original-image rows on the annotated TIF.
"""
from __future__ import annotations

import contextlib
import io

import numpy as np

import cv_pipeline as cv
import hybrid_v2 as h2
from lithology import extract as LX


def robust_depth_fit(pts) -> tuple:
    """(y, depth) label points -> (y -> depth function, info) with Pau's algorithm (lithology/extract.py
    calibrate_depth): RANSAC over pairs, MAD refinement, piecewise-linear when one line leaves rms > 3 px.
    Returns (None, None) when fewer than 2 labels agree."""
    pts = [(float(y), float(v)) for y, v in pts]
    if len(pts) < 2:
        return None, None
    ys, vs = np.array(pts, float).T
    order = np.argsort(ys)
    gaps = np.diff(ys[order])
    tol = max(4.0, 0.15 * np.median(gaps[gaps > 5])) if np.any(gaps > 5) else 6.0
    n = len(ys)
    rng = np.random.default_rng(0)
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    if len(pairs) > 4000:
        pairs = [tuple(rng.choice(n, 2, replace=False)) for _ in range(4000)]
    best = None
    for i, j in pairs:
        if ys[i] == ys[j] or vs[i] == vs[j]:
            continue
        a = (vs[j] - vs[i]) / (ys[j] - ys[i])
        if a <= 0:                                                  # depth increases downwards
            continue
        res = np.abs((vs - (vs[i] + a * (ys - ys[i]))) / a)
        inl = res < tol
        key = (inl.sum(), -res[inl].sum())
        if best is None or key > best[0]:
            best = (key, inl)
    if best is None or best[1].sum() < 2:
        return None, None
    inl = best[1]
    a, b = np.polyfit(ys[inl], vs[inl], 1)
    for _ in range(3):                                              # drop labels far from the line, refit
        r = np.abs((vs - (a * ys + b)) / a)
        mad = np.median(r[inl])
        keep = inl & (r <= max(2.0, 3.0 * 1.4826 * mad))
        if keep.sum() < max(2, 0.5 * inl.sum()) or keep.sum() == inl.sum():
            break
        inl = keep
        a, b = np.polyfit(ys[inl], vs[inl], 1)
    rms = float(np.sqrt(np.mean(((vs[inl] - (a * ys[inl] + b)) / a) ** 2)))
    model = "piecewise_linear" if rms > 3 and inl.sum() >= 4 else "linear"
    yi, vi = ys[inl][np.argsort(ys[inl])], vs[inl][np.argsort(ys[inl])]

    def y_to_depth(y):
        y = np.asarray(y, float)
        lin = a * y + b
        if model == "linear":
            return lin
        return np.where((y < yi[0]) | (y > yi[-1]), lin, np.interp(y, yi, vi))

    info = dict(a=float(a), b=float(b), n_used=int(inl.sum()), n_read=n, model=model, rms_px=round(rms, 2),
                points=[dict(y=float(y), depth=float(v), used=bool(u)) for y, v, u in zip(ys, vs, inl)])
    return y_to_depth, info


def read_depth_v3(image, box, cfg, ccfg) -> tuple:
    """Tesseract labels (fast) through Pau's robust fit; RapidOCR (Pau's reader) when they are too few or fit badly."""
    how, best = [], None
    try:
        tess = cv.calibrate_depth(image, box, ccfg)
        pts = [(p["y"], p["depth"]) for p in (tess.points if tess else [])]
        f, info = robust_depth_fit(pts)
        how.append(f"tesseract: {len(pts)} read, {info['n_used'] if info else 0} used")
        if info:
            info["engine"] = "tesseract"
            best = (f, info)
    except Exception as e:                                          # noqa: BLE001
        how.append(f"tesseract: {type(e).__name__}")
    good = best is not None and best[1]["n_used"] >= cfg.hybrid_depth_min_labels and best[1]["rms_px"] <= 5
    if not good:
        for nchunks in sorted({min(8, cfg.depth_max_chunks), cfg.depth_max_chunks}):
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    _, calib = LX.calibrate_depth(image, box, chunk=cfg.depth_chunk, max_chunks=nchunks)
                f, info = robust_depth_fit([(p["y"], p["depth"]) for p in calib["labels"]])
                how.append(f"rapidocr x{nchunks}: {info['n_used'] if info else 0} used")
                if info:
                    info["engine"] = "rapidocr"
                    if best is None or (info["n_used"], -info["rms_px"]) > (best[1]["n_used"], -best[1]["rms_px"]):
                        best = (f, info)
                    if info["n_used"] >= max(cfg.hybrid_depth_min_labels, 6):
                        break
            except Exception as e:                                  # noqa: BLE001
                how.append(f"rapidocr x{nchunks}: {type(e).__name__}")
    if best is None:
        return None, None, how
    f, info = best
    return (lambda y: float(f(y))), info, how


def process_log_v3(log, out_dir, cfg, ccfg, colours, on_progress) -> dict:
    return h2.process_log_v2(log, out_dir, cfg, ccfg, colours, on_progress, depth_reader=read_depth_v3,
                             method_name="hybrid_v3")
