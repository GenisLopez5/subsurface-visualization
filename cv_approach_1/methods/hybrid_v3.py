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
from PIL import Image, ImageDraw

from . import cv_pipeline as cv
from . import hybrid_v2 as h2
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


STRIP_ROWS = 3000          # Tesseract reads 3000-row strips of a depth column much more reliably than 8000-row ones


def _strip_starts(y0, y1, n):
    """Top rows of n evenly spaced STRIP_ROWS strips covering [y0, y1)."""
    if y1 - y0 <= STRIP_ROWS:
        return [y0]
    return sorted({int(round(v)) for v in np.linspace(y0, y1 - STRIP_ROWS, n)})


def _read_strips(image, box, ccfg, upright, starts, pts):
    x0, _, x1, y1 = box
    for s0 in starts:
        for q in cv._read_depth_labels(image, (x0, s0, x1, min(y1, s0 + STRIP_ROWS)), ccfg, upright=upright):
            if all(abs(q[0] - r[0]) > 20 or q[1] != r[1] for r in pts):      # skip duplicates from overlaps
                pts.append(q)
    return pts


def tesseract_depth_points(image, box, ccfg) -> tuple:
    """Depth labels read with Tesseract, in the orientation they are printed in, from evenly spaced 3000-row strips.
    1. probe: the middle strip is read both ways (sideways = rotated, upright); the way with more numbers wins
       (cv_pipeline reads sideways first and upright only after a failure, i.e. often the whole column twice);
    2. 8 evenly spaced strips are read that way (the probe's labels are kept);
    3. if fewer than 8 labels fit a line, 16 strips."""
    x0, y0, x1, y1 = box
    mid = max(y0, (y0 + y1) // 2 - STRIP_ROWS // 2)
    probe = {up: _read_strips(image, box, ccfg, up, [mid], []) for up in (False, True)}
    hits = {up: len(v) for up, v in probe.items()}
    upright = max(hits, key=lambda up: hits[up])
    pts = list(probe[upright])
    done = {mid}
    for n in (8, 16):
        starts = [s0 for s0 in _strip_starts(y0, y1, n) if all(abs(s0 - d) >= STRIP_ROWS // 2 for d in done)]
        _read_strips(image, box, ccfg, upright, starts, pts)
        done |= set(starts)
        _, info = robust_depth_fit(pts)
        if info and info["n_used"] >= 8:
            break
    return pts, upright, hits


def read_depth_v3(image, box, cfg, ccfg) -> tuple:
    """Tesseract labels (fast, orientation probed) through Pau's robust fit; the other orientation, then RapidOCR
    (Pau's reader), when they are too few or fit badly."""
    how, best = [], None
    try:
        pts, upright, hits = tesseract_depth_points(image, box, ccfg)
        f, info = robust_depth_fit(pts)
        how.append(f"tesseract {'upright' if upright else 'sideways'} (probe {hits[False]}/{hits[True]}): "
                   f"{len(pts)} read, {info['n_used'] if info else 0} used")
        if info:
            info["engine"] = "tesseract"
            best = (f, info)
        if best is None or best[1]["n_used"] < cfg.hybrid_depth_min_labels:      # probe misled: other orientation
            pts2 = cv._read_depth_labels(image, box, ccfg, upright=not upright)
            f2, info2 = robust_depth_fit(pts2)
            how.append(f"tesseract {'sideways' if upright else 'upright'}: {info2['n_used'] if info2 else 0} used")
            if info2 and (best is None or info2["n_used"] > best[1]["n_used"]):
                info2["engine"] = "tesseract"
                best = (f2, info2)
    except Exception as e:                                          # noqa: BLE001
        how.append(f"tesseract: {type(e).__name__}")
    good = best is not None and best[1]["n_used"] >= cfg.hybrid_depth_min_labels and best[1]["rms_px"] <= 5
    if not good:
        for nchunks in sorted({min(4, cfg.depth_max_chunks), min(8, cfg.depth_max_chunks), cfg.depth_max_chunks}):
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


def plot_legend_fast(swatches, groups, path) -> str | None:
    """The detected legend as a grid (same content as cv_pipeline.plot_legend, drawn with PIL: ~50x faster than
    matplotlib). Each swatch is shown 3x with its OCR name; look-alikes in red."""
    if not swatches:
        return None
    ncol = min(len(swatches), 8)
    cell_w, cell_h, z = 200, 92, 3
    rows = int(np.ceil(len(swatches) / ncol))
    out = Image.new("RGB", (ncol * cell_w, rows * cell_h), "white")
    d = ImageDraw.Draw(out)
    font = cv._font(12)
    for i, s in enumerate(swatches):
        cx, cy = (i % ncol) * cell_w, (i // ncol) * cell_h
        look = [swatches[j].name for j in groups.get(i, {i}) if j != i]
        d.text((cx + cell_w // 2, cy + 4), s.name[:28], fill=(179, 38, 30) if look else (31, 31, 30), font=font, anchor="mt")
        if look:
            d.text((cx + cell_w // 2, cy + 19), ("≈ " + ", ".join(look))[:30], fill=(179, 38, 30), font=font, anchor="mt")
        tile = Image.fromarray(s.rgb) if s.rgb is not None and s.rgb.size else \
            Image.fromarray(((1 - s.ink) * 255).astype(np.uint8)).convert("RGB")
        k = min(z, (cell_w - 10) / max(1, tile.width), 50 / max(1, tile.height))
        tile = tile.resize((max(1, int(tile.width * k)), max(1, int(tile.height * k))), Image.NEAREST)
        out.paste(tile, (cx + (cell_w - tile.width) // 2, cy + 36))
    out.save(path)
    return str(path)


def process_log_v3(log, out_dir, cfg, ccfg, colours, on_progress) -> dict:
    return h2.process_log_v2(log, out_dir, cfg, ccfg, colours, on_progress, depth_reader=read_depth_v3,
                             method_name="hybrid_v3", parallel_depth=True, legend_plot=plot_legend_fast)
