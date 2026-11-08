#!/usr/bin/env python3
"""
Extract lithology intervals from a mud-log image, given its bounding-box annotation.

    python -m lithology extract IMAGE ANNOTATION.json [-o out.json] [--csv out.csv] [--preview out.png]

IMAGE can be a .tif/.png/... scan or a .pdf (first page is rendered at the size the JSON says).

Pipeline (classical computer vision + OCR, no training needed):
  1. Depth calibration  - OCR the numbers in `depth_column`, robust-fit  depth = a * y + b.
  2. Legend parsing     - OCR the labels in `lithology_legend`, find the pattern swatch that belongs
                          to each label, and compute a texture/colour "fingerprint" for every swatch.
  3. Column reading     - slide a window down `interpreted_lithology_column`, fingerprint every
                          window and give it the label of the closest legend swatch.
  4. Intervals          - smooth the per-row labels, merge them into runs, convert pixel rows to
                          depth with step 1, and write the result as JSON (and optionally CSV).
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import logging
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

Image.MAX_IMAGE_PIXELS = None  # the scans are huge on purpose

# ----------------------------------------------------------------------------------------------
# Image loading
# ----------------------------------------------------------------------------------------------


def load_page(path: Path, ann: dict) -> Image.Image:
    """Open an image lazily, or render page 1 of a PDF at the pixel size stored in the annotation."""
    if path.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium

        page = pdfium.PdfDocument(str(path))[0]
        w_pt, _ = page.get_size()
        img = page.render(scale=ann["width"] / w_pt).to_pil()
        if img.size != (ann["width"], ann["height"]):
            img = img.resize((ann["width"], ann["height"]))
        return img
    return Image.open(path)


def crop_rgb(img: Image.Image, box, strip: int = 4000) -> np.ndarray:
    """Crop (x0, y0, x1, y1) as an HxWx3 uint8 array, converting in strips so huge 1-bit scans stay cheap."""
    x0, y0, x1, y1 = (int(v) for v in box)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(img.width, x1), min(img.height, y1)
    out = np.empty((y1 - y0, x1 - x0, 3), np.uint8)
    for ys in range(y0, y1, strip):
        ye = min(ys + strip, y1)
        out[ys - y0 : ye - y0] = np.asarray(img.crop((x0, ys, x1, ye)).convert("RGB"))
    return out


# ----------------------------------------------------------------------------------------------
# OCR (RapidOCR = PaddleOCR models on onnxruntime; pip-installable, no system packages)
# ----------------------------------------------------------------------------------------------

# RapidOCR's default text detector scales an image up until its SHORT side is 736 px. A tall, narrow
# depth-column chunk (1600 x 130) becomes ~9000 x 736 that way: up to 3.6 GB of RAM and ~4 s per
# chunk. Depth chunks are therefore detected at their native size ("depth" engine): same depth
# scale on the test logs, 10-17x faster, a third of the memory. The legend keeps the default
# ("legend" engine): it is one image per log, and its small text needs the upscaling.
_OCR_PARAMS = {
    "depth": {"Det.limit_type": "max", "Det.limit_side_len": 4000},
    "legend": {},
}
_OCR: dict = {}


def ocr(arr: np.ndarray, kind: str = "legend"):
    """Return [(x0, y0, x1, y1, text, score)] for an RGB array."""
    if kind not in _OCR:
        from rapidocr import RapidOCR

        _OCR[kind] = RapidOCR(params=_OCR_PARAMS[kind]) if _OCR_PARAMS[kind] else RapidOCR()
        logging.getLogger("RapidOCR").setLevel(logging.ERROR)
    res = _OCR[kind](np.ascontiguousarray(arr))
    if res.boxes is None:
        return []
    out = []
    for b, t, s in zip(res.boxes, res.txts, res.scores):
        b = np.asarray(b)
        out.append((b[:, 0].min(), b[:, 1].min(), b[:, 0].max(), b[:, 1].max(), t, float(s)))
    return out


# ----------------------------------------------------------------------------------------------
# 1. Depth calibration
# ----------------------------------------------------------------------------------------------


def parse_depth(text: str):
    t = re.sub(r"[\s'’`\"]", "", text)
    if not t or sum(c.isdigit() for c in t) < 0.8 * len(t):
        return None
    t = re.sub(r"[^\d,.]", "", t)
    if re.fullmatch(r"\d{1,3}([,.]\d{3})+", t):  # 9,060  or 9.060
        t = re.sub(r"[,.]", "", t)
    if not re.fullmatch(r"\d{2,6}", t):
        return None
    return float(t)


def _ocr_depth_chunk(col: np.ndarray, rot: int):
    """OCR one chunk of the depth column rotated by rot*90deg CCW. Returns [(row_in_chunk, value)]."""
    H = col.shape[0]
    found = []
    for x0, y0, x1, y1, text, score in ocr(np.rot90(col, rot), kind="depth"):
        v = parse_depth(text)
        if v is None or score < 0.5:
            continue
        # back to chunk rows: rot0 -> y, rot1 (CCW) -> x, rot3 (CW) -> H-1-x
        if rot == 0:
            a, b = y0, y1
        elif rot == 1:
            a, b = x0, x1
        else:
            a, b = H - 1 - x1, H - 1 - x0
        if a <= 2 or b >= H - 3:  # touches the chunk edge -> probably a cut-off label
            continue
        found.append(((a + b) / 2.0, v))
    return found


def calibrate_depth(img, box, chunk=1600, overlap=300, max_chunks=40, verbose=False):
    x0, y0, x1, y1 = box
    pad = max(6, (x1 - x0) // 4)  # labels sometimes spill over the column edges
    xa, xb = max(0, x0 - pad), min(img.width, x1 + pad)
    starts = list(range(y0, max(y0 + 1, y1 - overlap), chunk - overlap))
    if len(starts) > max_chunks:  # very tall logs: sample chunks evenly, a linear fit needs few labels
        starts = [starts[i] for i in np.linspace(0, len(starts) - 1, max_chunks).round().astype(int)]

    # choose the text orientation on a few sample chunks
    probe = [starts[i] for i in np.linspace(0, len(starts) - 1, min(3, len(starts))).round().astype(int)]
    best_rot, best_n = 0, -1
    for rot in (0, 1, 3):
        n = sum(len(_ocr_depth_chunk(crop_rgb(img, (xa, s, xb, min(s + chunk, y1))), rot)) for s in probe)
        if n > best_n:
            best_rot, best_n = rot, n
        if n >= 2 * len(probe):  # clearly readable this way up, no need to try the others
            break
    if verbose:
        print(f"[depth] orientation = {best_rot * 90} deg CCW ({best_n} numbers on probe chunks)", file=sys.stderr)

    pts = []
    for s in starts:
        for r, v in _ocr_depth_chunk(crop_rgb(img, (xa, s, xb, min(s + chunk, y1))), best_rot):
            y = s + r
            if not any(v == pv and abs(y - py) < 15 for py, pv in pts):  # dedupe overlap hits
                pts.append((y, v))
    if len(pts) < 2:
        raise RuntimeError(f"depth calibration failed: only {len(pts)} depth labels read by OCR")
    ys, vs = np.array(pts, float).T

    # RANSAC over pairs: find the line that most labels agree with (kills OCR misreads)
    order = np.argsort(ys)
    gaps = np.diff(ys[order])
    tol = max(4.0, 0.15 * np.median(gaps[gaps > 5])) if np.any(gaps > 5) else 6.0
    rng = np.random.default_rng(0)
    n = len(ys)
    pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
    if len(pairs) > 4000:
        pairs = [tuple(rng.choice(n, 2, replace=False)) for _ in range(4000)]
    best = None
    for i, j in pairs:
        if ys[i] == ys[j] or vs[i] == vs[j]:
            continue
        a = (vs[j] - vs[i]) / (ys[j] - ys[i])
        res = np.abs((vs - (vs[i] + a * (ys - ys[i]))) / a)  # residual in pixels
        inl = res < tol
        key = (inl.sum(), -res[inl].sum())
        if best is None or key > best[0]:
            best = (key, inl)
    inl = best[1]
    if inl.sum() < 2:
        raise RuntimeError("depth calibration failed: no two labels agree on a scale")
    a, b = np.polyfit(ys[inl], vs[inl], 1)
    # Second pass: RANSAC's tolerance is loose (it only has to reject misread numbers). Drop labels
    # that sit far from the line compared with the others (OCR box centre off), then refit.
    for _ in range(3):
        r = np.abs((vs - (a * ys + b)) / a)
        mad = np.median(r[inl])
        keep = inl & (r <= max(2.0, 3.0 * 1.4826 * mad))
        if keep.sum() < max(2, 0.5 * inl.sum()) or keep.sum() == inl.sum():
            break
        inl = keep
        a, b = np.polyfit(ys[inl], vs[inl], 1)
    resid_px = (vs[inl] - (a * ys[inl] + b)) / a
    calib = {
        "model": "linear",
        "depth_per_px": float(a),
        "px_per_depth_unit": float(1 / a),
        "intercept": float(b),
        "rms_residual_px": float(np.sqrt(np.mean(resid_px**2))),
        "n_labels_used": int(inl.sum()),
        "n_labels_rejected": int((~inl).sum()),
        "labels": [{"y": round(float(y), 1), "depth": float(v), "used": bool(u)} for y, v, u in zip(ys, vs, inl)],
    }
    # If a single line doesn't explain the labels (scale change / depth break) go piecewise.
    if calib["rms_residual_px"] > 3 and inl.sum() >= 4:
        calib["model"] = "piecewise_linear"
    if verbose:
        print(
            f"[depth] {inl.sum()} labels used, {(~inl).sum()} rejected, {1 / a:.2f} px per unit, "
            f"rms {calib['rms_residual_px']:.2f} px, model {calib['model']}",
            file=sys.stderr,
        )

    yi, vi = ys[inl][np.argsort(ys[inl])], vs[inl][np.argsort(ys[inl])]

    def y_to_depth(y):
        y = np.asarray(y, float)
        lin = a * y + b
        if calib["model"] == "linear":
            return lin
        inside = np.interp(y, yi, vi)
        return np.where((y < yi[0]) | (y > yi[-1]), lin, inside)

    return y_to_depth, calib


# ----------------------------------------------------------------------------------------------
# Texture / colour fingerprint
# ----------------------------------------------------------------------------------------------
# Per row we sum simple counts; a window's fingerprint is computed from its summed counts, so a
# sliding window over a 300k-row column is just a cumulative sum.
#
# The legend and the column are often drawn at different scales / stroke thicknesses (especially
# on scanned B/W logs), so the fingerprint uses things that survive that:
#   fill colour  - mean colour of the non-stroke pixels (the strongest cue on colour logs)
#   ink          - fraction of dark stroke pixels (weak: depends on stroke thickness)
#   edges        - gradient energy per pixel (weak)
#   orientation  - histogram of stroke-edge directions in 4 bins:
#                  horizontal dashes (shale/clay) -> mostly vertical gradients,
#                  vertical bars (siltstone)      -> mostly horizontal gradients,
#                  bricks (limestone)             -> both, few diagonals,
#                  dots (sand)                    -> all four bins, diagonal lines -> diagonal bins.

N_ROW_FEATS = 13  # rgb_noink(3), n_noink, rgb_all(3), n_ink, edge_mag, orientation bins(4)
N_FEATS = 9  # fill rgb(3), ink, edge, orientation(4)
LUM = np.array([0.299, 0.587, 0.114], np.float32)


def row_counts(arr: np.ndarray) -> np.ndarray:
    """arr HxWx3 uint8 -> H x N_ROW_FEATS per-row sums."""
    f = arr.astype(np.float32) / 255.0
    lum = f @ LUM
    ink = lum < 0.45
    H, W = ink.shape
    out = np.zeros((H, N_ROW_FEATS), np.float32)
    noink = ~ink
    out[:, 0:3] = (f * noink[..., None]).sum(axis=1)
    out[:, 3] = noink.sum(axis=1)
    out[:, 4:7] = f.sum(axis=1)
    out[:, 7] = ink.sum(axis=1)
    if H >= 3 and W >= 3:
        gx = np.zeros_like(lum)
        gy = np.zeros_like(lum)
        gx[:, 1:-1] = lum[:, 2:] - lum[:, :-2]
        gy[1:-1, :] = lum[2:, :] - lum[:-2, :]
        mag = np.hypot(gx, gy)
        mag[mag < 0.2] = 0  # ignore scan noise / dithering
        ang = np.mod(np.arctan2(gy, gx), np.pi)
        b = np.round(ang / (np.pi / 4)).astype(np.int8) % 4
        out[:, 8] = mag.sum(axis=1)
        for k in range(4):
            out[:, 9 + k] = (mag * (b == k)).sum(axis=1)
    return out


def counts_to_features(s: np.ndarray, n_pix) -> np.ndarray:
    """Summed counts (..., N_ROW_FEATS) -> fingerprint (..., N_FEATS)."""
    n_pix = np.asarray(n_pix, np.float32)[..., None]
    n_noink = s[..., 3:4]
    fill = np.where(n_noink > 0.1 * n_pix, s[..., 0:3] / np.maximum(n_noink, 1), s[..., 4:7] / n_pix)
    ink = s[..., 7:8] / n_pix
    edge = s[..., 8:9] / n_pix
    orient = s[..., 9:13] / np.maximum(s[..., 8:9], 1e-6)
    return np.concatenate([fill, ink, edge, orient], axis=-1)


def feature_distance(F: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Distance between fingerprints F (N x D) and one reference (D)."""
    dc = np.linalg.norm(F[:, 0:3] - ref[0:3], axis=1)
    di = np.abs(F[:, 3] - ref[3])
    de = np.abs(F[:, 4] - ref[4])
    # orientation only means something where the window actually has strokes; references without
    # strokes (blank, solid coal) carry a uniform histogram, so a patterned window is far from them
    gate = np.clip(F[:, 4] / 0.02, 0, 1)
    do = np.abs(F[:, 5:9] - ref[5:9]).sum(axis=1)  # L1 between distributions, 0..2
    return 3.0 * dc + 1.0 * di + 0.5 * de + 1.5 * gate * do


def shrink(box, frac=0.12, min_px=2):
    x0, y0, x1, y1 = box
    dx = max(min_px, int((x1 - x0) * frac))
    dy = max(min_px, int((y1 - y0) * frac))
    if x1 - x0 > 2 * dx + 2:
        x0, x1 = x0 + dx, x1 - dx
    if y1 - y0 > 2 * dy + 2:
        y0, y1 = y0 + dy, y1 - dy
    return (x0, y0, x1, y1)


# ----------------------------------------------------------------------------------------------
# 2. Legend parsing
# ----------------------------------------------------------------------------------------------

LITHOLOGY_VOCAB = """anhydrite argillaceous basalt bentonite breccia calcareous carbonate cement chalk chert
claystone clay coal conglomerate dolomite dolostone evaporites gypsum halite igneous limestone marl marlstone
metamorphic mudstone salt sand sandstone shale siltstone silt silty sandy siderite limonite tuff till unknown
volcanic granite gravel lignite ash fossils pyrite glauconite gray grey colored coloured and or with""".split()


def clean_label(text: str) -> str:
    t = re.sub(r"\b([A-Za-z]) (?=[a-z])", r"\1", text)  # "S lltstone" -> "Slltstone" (spaced-out fonts)
    t = re.sub(r"\s+", " ", t).strip(" .:-_")
    words = []
    for w in t.split(" "):
        core = re.sub(r"[^A-Za-z]", "", w).lower()
        m = difflib.get_close_matches(core, LITHOLOGY_VOCAB, n=1, cutoff=0.75) if len(core) > 3 else []
        if m and m[0] != core:
            fixed = m[0]
            w = fixed.upper() if w.isupper() else fixed.capitalize() if w[:1].isupper() else fixed
        words.append(w)
    return " ".join(words)


def parse_legend(img, box, verbose=False, debug: dict | None = None):
    """Legend entries. If `debug` is a dict, it is filled with the intermediate steps (OCR words,
    swatch candidates, rejected blobs, swatch->label links, continuation joins), in legend-crop pixels."""
    lx0, ly0, lx1, ly1 = box
    arr = crop_rgb(img, box)
    H, W = arr.shape[:2]
    up = 2 if min(H, W) < 700 else 1  # small text OCRs much better upscaled
    big = np.asarray(Image.fromarray(arr).resize((W * up, H * up), Image.Resampling.LANCZOS)) if up > 1 else arr
    words = []
    for x0, y0, x1, y1, t, s in ocr(big):
        if s >= 0.5 and re.search(r"[A-Za-z]{2,}", t):
            words.append({"box": [x0 / up, y0 / up, x1 / up, y1 / up], "text": t})

    # ink mask without the text, then connected components = candidate swatches
    flat = arr.reshape(-1, 3)
    vals, counts = np.unique(flat[:: max(1, len(flat) // 200000)], axis=0, return_counts=True)
    bg = vals[counts.argmax()].astype(int)
    ink = np.abs(arr.astype(int) - bg).max(axis=2) > 40
    for w in words:
        x0, y0, x1, y1 = w["box"]
        ink[max(0, int(y0) - 2) : int(y1) + 3, max(0, int(x0) - 2) : int(x1) + 3] = False
    # join pattern fragments sideways only: legend rows can be just a few px apart vertically
    lab, n = ndimage.label(ndimage.binary_dilation(ink, structure=np.ones((1, 7), bool)))
    th_med = float(np.median([w["box"][3] - w["box"][1] for w in words])) if words else 10.0
    min_side = max(6, 0.5 * th_med)  # smaller blobs are leftovers of letters, not swatches
    comps = []
    rejected = []
    for sl in ndimage.find_objects(lab):
        y0, y1, x0, x1 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop
        w, h = x1 - x0, y1 - y0
        if w < min_side or h < min_side or w > 0.6 * W or h > 0.6 * H:  # specks, or the legend's outer frame
            if debug is not None and max(w, h) >= 4:
                rejected.append({"box": [int(x0), int(y0), int(x1), int(y1)],
                                 "why": "too big (frame?)" if w > 0.6 * W or h > 0.6 * H else "too small"})
            continue
        comps.append([x0, y0, x1, y1])

    # assign each swatch to the label right of it, or below it (closest wins)
    owner = {}
    for ci, (x0, y0, x1, y1) in enumerate(comps):
        sw, sh = x1 - x0, y1 - y0
        best = None
        for wi, wd in enumerate(words):
            tx0, ty0, tx1, ty1 = wd["box"]
            th = ty1 - ty0
            ov_y = min(y1, ty1) - max(y0, ty0)
            if tx0 >= x1 - 3 and ov_y > 0.3 * min(sh, th):
                d = tx0 - x1
                if d <= max(sw, 3 * th, 40):
                    best = min(best or (1e9, -1), (d, wi))
            cx = (tx0 + tx1) / 2
            if ty0 >= y1 - 3 and x0 - 0.25 * sw <= cx <= x1 + 0.25 * sw:
                # a swatch belongs to the *first* line of a multi-line label below it
                if any(
                    j != wi and words[j]["box"][1] >= y1 - 3 and words[j]["box"][3] <= ty0 + 2
                    and min(words[j]["box"][2], tx1) - max(words[j]["box"][0], tx0) > 0
                    for j in range(len(words))
                ):
                    continue
                d = ty0 - y1
                if d <= max(sh, 3 * th, 40):
                    best = min(best or (1e9, -1), (d, wi))
        if best:
            wi = best[1]
            if wi not in owner or best[0] < owner[wi][0]:
                owner[wi] = (best[0], ci)
        if debug is not None:
            debug.setdefault("claims", []).append({"swatch": ci, "word": int(best[1]) if best else None,
                                                   "dist": float(best[0]) if best else None})

    # labels without a swatch are continuation lines/words of a neighbouring label ("Sand and" / "Sandstone")
    # Each orphan joins the closest text piece (owner word or an already-joined orphan), top to bottom.
    names = {wi: words[wi]["text"] for wi in owner}
    pieces = [(words[wi]["box"], wi) for wi in owner]  # (box, owning label)
    for oi in sorted((i for i in range(len(words)) if i not in owner), key=lambda i: (words[i]["box"][1], words[i]["box"][0])):
        ox0, oy0, ox1, oy1 = words[oi]["box"]
        th = oy1 - oy0
        best = None
        for (bx0, by0, bx1, by1), wi in pieces:
            gap_x, gap_y = ox0 - bx1, oy0 - by1
            if min(oy1, by1) - max(oy0, by0) > 0.5 * th and 0 <= gap_x < 1.5 * th:  # same line, to the right
                d = gap_x
            elif -0.5 * th <= gap_y < 1.2 * th and oy0 > by0 and min(ox1, bx1) - max(ox0, bx0) > 0:  # next line
                d = abs(gap_y)
            else:
                continue
            if best is None or d < best[0]:
                best = (d, wi)
        if best:
            names[best[1]] += " " + words[oi]["text"]
            pieces.append((words[oi]["box"], best[1]))
        if debug is not None:
            debug.setdefault("orphans", []).append({"word": int(oi), "joined_to": int(best[1]) if best else None})

    entries = []
    for wi, (_, ci) in owner.items():
        sbox = shrink(comps[ci])
        sw = arr[sbox[1] : sbox[3], sbox[0] : sbox[2]]
        feat = counts_to_features(row_counts(sw).sum(0), sw.shape[0] * sw.shape[1])
        if feat[4] < 0.005:
            feat[5:9] = 0.25  # no strokes (solid fill): orientation undefined
        entries.append(
            {
                "label": clean_label(names[wi]),
                "raw_text": names[wi],
                "swatch_bbox": [int(comps[ci][0] + lx0), int(comps[ci][1] + ly0), int(comps[ci][2] + lx0), int(comps[ci][3] + ly0)],
                "swatch_height": int(sbox[3] - sbox[1]),
                "feature": feat,
            }
        )
    if debug is not None:
        debug.update({
            "crop_box": [int(v) for v in box], "crop_size": [int(W), int(H)], "upscale": up,
            "background": [int(v) for v in bg], "min_side": float(min_side),
            "words": [{"box": [float(v) for v in w["box"]], "text": w["text"]} for w in words],
            "swatches": [[int(v) for v in c] for c in comps],
            "rejected": rejected,
            "links": [{"word": int(wi), "swatch": int(ci), "dist": float(d)} for wi, (d, ci) in owner.items()],
            "unowned_swatches": [i for i in range(len(comps)) if i not in {ci for _, ci in owner.values()}],
        })
        debug.setdefault("orphans", [])
        debug.setdefault("claims", [])
    if verbose:
        print(f"[legend] {len(words)} text boxes, {len(comps)} swatch candidates, {len(entries)} entries:", file=sys.stderr)
        for e in entries:
            print(f"         {e['label']!r:28s} fill={np.round(e['feature'][:3], 2)} ink={e['feature'][3]:.3f} orient={np.round(e['feature'][5:], 2)}", file=sys.stderr)
    return entries


# ----------------------------------------------------------------------------------------------
# 3 + 4. Read the lithology column and build intervals
# ----------------------------------------------------------------------------------------------

BLANK = "__blank__"


def classify_column(img, box, entries, win=None, verbose=False):
    """Segment the lithology column and label every segment.

    1. texture cuts: classify a sliding window (height `win`) per row, majority-filter it, and cut
       where the label changes (finds boundaries between patterns of the same colour, e.g. B/W logs);
    2. colour cuts: cut where the fill colour just above a row differs from just below it
       (finds thin beds on colour logs, which a big window would blur away);
    3. classify each segment between cuts using *all* its pixels, then merge equal neighbours.
    """
    x0, y0, x1, y1 = box
    cx0, _, cx1, _ = shrink((x0, y0, x1, y1), frac=0.06, min_px=2)
    arr = crop_rgb(img, (cx0, y0, cx1, y1))
    H, W = arr.shape[:2]
    counts = np.concatenate([row_counts(arr[s : s + 8000]) for s in range(0, H, 8000)])
    del arr

    # an extra "blank" class = the column is empty here (nothing interpreted)
    blank_feat = np.zeros(N_FEATS, np.float32)
    blank_feat[0:3] = 1.0
    blank_feat[5:9] = 0.25
    classes = [(BLANK, blank_feat)]
    for e in entries:
        f = e["feature"]
        if f[3] < 0.015 and np.linalg.norm(f[0:3] - 1.0) < 0.1:  # white box with a tiny symbol
            e["ignored"] = "looks blank (symbol, not a lithology pattern)"
        elif any(e["label"] == c[0] for c in classes):
            e["ignored"] = "duplicate label"
        else:
            classes.append((e["label"], f))
    refs = [f for _, f in classes]

    def distances(F):
        return np.stack([feature_distance(F, f) for f in refs], axis=1)

    if win is None:
        hs = [e["swatch_height"] for e in entries if "ignored" not in e]
        win = int(np.clip(np.median(hs) if hs else 20, 8, 40))
    c = np.cumsum(np.vstack([np.zeros((1, counts.shape[1]), np.float64), counts]), axis=0)

    def seg_features(lo, hi):
        return counts_to_features((c[hi] - c[lo]).astype(np.float32), (hi - lo) * W)

    # 1. texture cuts
    rows = np.arange(H)
    lo = np.clip(rows - win // 2, 0, H)
    hi = np.clip(rows + (win - win // 2), 0, H)
    lab = distances(seg_features(lo, hi)).argmin(axis=1)
    K = len(classes)
    oc = np.zeros((H + 1, K), np.int32)
    oc[rows + 1, lab] = 1
    oc = np.cumsum(oc, axis=0)
    lab = (oc[hi] - oc[lo]).argmax(axis=1)
    cuts = set((np.flatnonzero(np.diff(lab)) + 1).tolist())
    n_tex = len(cuts)

    # 2. colour cuts (fill colour of the k rows above vs the k rows below)
    k = 3
    y = np.arange(k, H - k + 1)
    na, nb = c[y, 3] - c[y - k, 3], c[y + k, 3] - c[y, 3]
    fa = (c[y, 0:3] - c[y - k, 0:3]) / np.maximum(na, 1)[:, None]
    fb = (c[y + k, 0:3] - c[y, 0:3]) / np.maximum(nb, 1)[:, None]
    score = np.linalg.norm(fa - fb, axis=1)
    score[(na < 0.2 * k * W) | (nb < 0.2 * k * W)] = 0  # rows that are mostly stroke: unreliable fill
    peak = (score > 0.2) & (score == ndimage.maximum_filter1d(score, 2 * k + 1))
    colour_cuts = set((y[peak]).tolist())
    cuts |= colour_cuts

    # 3. classify segments, merge equal neighbours
    edges = np.array(sorted(cuts | {0, H}))
    edges = edges[np.r_[True, np.diff(edges) >= 2]]  # drop 1-px slivers
    if edges[-1] != H:
        edges[-1] = H
    s_lo, s_hi = edges[:-1], edges[1:]
    D = distances(seg_features(s_lo, s_hi))
    seg_lab = D.argmin(axis=1)
    srt = np.sort(D, axis=1)
    conf = (srt[:, 1] - srt[:, 0]) / np.maximum(srt[:, 1], 1e-6)  # 0 = ambiguous, 1 = clear-cut

    def merge_equal(segs):
        out = []
        for l, a, b, cf in segs:
            if out and out[-1][0] == l:
                pl, pa, pb, pc = out[-1]
                out[-1] = (l, pa, b, (pc * (pb - pa) + cf * (b - a)) / (b - pa))
            else:
                out.append((int(l), int(a), int(b), float(cf)))
        return out

    runs = merge_equal(zip(seg_lab, s_lo, s_hi, conf))

    # A window straddling two patterns can look like a third one: drop such slivers (thinner than
    # the window) into the neighbour they resemble most -- unless the sliver's fill colour differs
    # from both neighbours, in which case it is a real thin bed on a colour log.
    refs_arr = np.stack(refs)
    for _ in range(20):  # each pass relabels the shortest slivers; a few passes always suffice
        if len(runs) < 2:
            break
        R = np.array([(r[1], r[2]) for r in runs])
        L = np.array([r[0] for r in runs])
        F = seg_features(R[:, 0], R[:, 1])
        fills = F[:, 0:3]
        n = len(runs)
        d_prev = np.full(n, np.inf)
        d_next = np.full(n, np.inf)
        d_prev[1:] = np.linalg.norm(fills[1:] - fills[:-1], axis=1)
        d_next[:-1] = d_prev[1:]
        short = (R[:, 1] - R[:, 0]) < win
        sliver = short & (np.minimum(d_prev, d_next) < 0.15)
        # don't touch two adjacent slivers in the same pass (their neighbours are changing)
        sliver &= ~np.r_[False, sliver[:-1]]
        if not sliver.any():
            break
        idx = np.flatnonzero(sliver)
        # which neighbour does the sliver's texture resemble more?
        prev_l = np.where(idx > 0, L[np.maximum(idx - 1, 0)], -1)
        next_l = np.where(idx < n - 1, L[np.minimum(idx + 1, n - 1)], -1)
        new_l = []
        for k, i in enumerate(idx):
            opts = [l for l in (prev_l[k], next_l[k]) if l >= 0]
            dists = [feature_distance(F[i : i + 1], refs_arr[l])[0] for l in opts]
            new_l.append(opts[int(np.argmin(dists))])
        for i, l in zip(idx, new_l):
            r = runs[i]
            runs[i] = (int(l), r[1], r[2], r[3])
        runs = merge_equal(runs)
    if verbose:
        print(
            f"[column] {H} rows, window {win} px, {K - 1} classes, {n_tex} texture cuts + "
            f"{len(colour_cuts)} colour cuts -> {len(runs)} intervals",
            file=sys.stderr,
        )
    return [(classes[l][0], a + y0, b + y0, float(cf)) for l, a, b, cf in runs]


def _runs(lab):
    edges = np.flatnonzero(np.diff(lab)) + 1
    starts = np.r_[0, edges]
    ends = np.r_[edges, len(lab)]
    return [(int(lab[s]), int(s), int(e)) for s, e in zip(starts, ends)]


# ----------------------------------------------------------------------------------------------
# Legend method
# ----------------------------------------------------------------------------------------------
# Several legend parsers exist; compare them on the review page (webui/legend.html). The one used
# for extraction is a server setting, not something API users choose: LITHO_LEGEND_METHOD
# (environment or .env), default "grid". All return the same entries.
#   grid   columns of swatches, text read per column strip (legend_grid.py)      <- default
#   boxes  proximity grouping: grow ink until each swatch touches its text (legend_boxes.py)
#   xycut  recursive cuts along empty lines, one swatch per cell (legend_boxes.py)
#   old    parse_legend above: nearest label per swatch

LEGEND_METHODS = ("grid", "boxes", "xycut", "old")
DEFAULT_LEGEND_METHOD = "grid"


def legend_method(name: str | None = None) -> str:
    """The legend method to use: `name` if given, else LITHO_LEGEND_METHOD, else the default."""
    import os

    m = (name or os.environ.get("LITHO_LEGEND_METHOD") or DEFAULT_LEGEND_METHOD).strip().lower()
    if m not in LEGEND_METHODS:
        raise ValueError(f"unknown legend method {m!r} (choose from {', '.join(LEGEND_METHODS)})")
    return m


def legend_parser(name: str | None = None):
    """The parse function for a legend method: f(img, box, verbose=False, debug=None) -> entries."""
    m = legend_method(name)
    if m == "old":
        return parse_legend
    if m == "grid":
        from .legend_grid import parse_legend_grid
        return parse_legend_grid
    from .legend_boxes import parse_legend_boxes, parse_legend_xycut
    return {"boxes": parse_legend_boxes, "xycut": parse_legend_xycut}[m]


# ----------------------------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------------------------


def extract(image_path, annotation_path, verbose=False, round_depth=0.1, keep_blank=False, legend_method_name=None):
    ann = json.loads(Path(annotation_path).read_text())
    boxes = ann["bounding_boxes"]
    img = load_page(Path(image_path), ann)
    if (img.width, img.height) != (ann["width"], ann["height"]):
        print(f"warning: image is {img.size}, annotation says {(ann['width'], ann['height'])}", file=sys.stderr)

    y_to_depth, calib = calibrate_depth(img, boxes["depth_column"], verbose=verbose)
    method = legend_method(legend_method_name)
    entries = legend_parser(method)(img, boxes["lithology_legend"], verbose=verbose)
    if not entries:
        raise RuntimeError("no legend entries found")
    runs = classify_column(img, boxes["interpreted_lithology_column"], entries, verbose=verbose)

    def rd(v):
        return float(np.round(v / round_depth) * round_depth) if round_depth else float(v)

    intervals = []
    for name, top, bot, conf in runs:
        if name == BLANK and not keep_blank:
            continue
        intervals.append(
            {
                "top_depth": round(rd(y_to_depth(top)), 4),
                "bottom_depth": round(rd(y_to_depth(bot)), 4),
                "lithology": "Blank" if name == BLANK else name,
                "top_y": int(top),
                "bottom_y": int(bot),
                "confidence": round(conf, 3),
            }
        )
    return {
        "image_name": ann.get("image_name", Path(image_path).name),
        "width": ann["width"],
        "height": ann["height"],
        "bounding_boxes": boxes,
        "depth_calibration": calib,
        "legend_method": method,
        "legend": [
            {k: v for k, v in e.items() if k not in ("feature", "swatch_height")} for e in entries
        ],
        "intervals": intervals,
    }


def write_csv(result, path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["top_depth", "bottom_depth", "lithology", "top_y", "bottom_y"])
        for r in result["intervals"]:
            w.writerow([f"{r['top_depth']:g}", f"{r['bottom_depth']:g}", r["lithology"], r["top_y"], r["bottom_y"]])


def write_preview(image_path, result, path, scale=None):
    """Side-by-side strip: original lithology column | predicted colours, with depth labels."""
    from PIL import ImageDraw

    ann = {"width": result["width"], "height": result["height"]}
    img = load_page(Path(image_path), ann)
    b = result["bounding_boxes"]
    lx0, ly0, lx1, ly1 = b["interpreted_lithology_column"]
    dx0 = b["depth_column"][0]
    x0, x1 = min(dx0, lx0) - 10, lx1 + 10
    H = ly1 - ly0
    scale = scale or min(1.0, 6000 / H)
    col = Image.fromarray(crop_rgb(img, (x0, ly0, x1, ly1))).resize((max(1, int((x1 - x0) * scale)), max(1, int(H * scale))))
    pal = [(40, 160, 60), (160, 100, 40), (240, 190, 0), (150, 60, 170), (0, 150, 150), (230, 110, 20), (200, 60, 120), (110, 110, 110), (30, 90, 200), (220, 40, 40)]
    names = list(dict.fromkeys(r["lithology"] for r in result["intervals"]))
    cmap = {n: pal[i % len(pal)] for i, n in enumerate(names)}
    out = Image.new("RGB", (col.width + 260, col.height + 20 * (len(names) + 1)), "white")
    out.paste(col, (0, 20 * (len(names) + 1)))
    d = ImageDraw.Draw(out)
    for i, n in enumerate(names):
        d.rectangle([5, 4 + 20 * i, 19, 18 + 20 * i], fill=cmap[n])
        d.text((25, 5 + 20 * i), n, fill="black")
    off = 20 * (len(names) + 1)
    for r in result["intervals"]:
        t, bt = (r["top_y"] - ly0) * scale + off, (r["bottom_y"] - ly0) * scale + off
        d.rectangle([col.width + 5, t, col.width + 60, bt], fill=cmap[r["lithology"]])
        d.line([(col.width + 60, t), (col.width + 70, t)], fill="black")
        if bt - t > 0 or True:
            d.text((col.width + 75, t), f"{r['top_depth']:g} {r['lithology'][:18]}", fill="black")
    out.save(path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", help="log image (.tif/.png/...) or .pdf")
    ap.add_argument("annotation", help="bounding-box JSON for that image")
    ap.add_argument("-o", "--output", help="output JSON (default: stdout)")
    ap.add_argument("--csv", help="also write the intervals as CSV (same columns as example.csv)")
    ap.add_argument("--preview", help="write a PNG comparing the column with the prediction")
    ap.add_argument("--round-depth", type=float, default=0.1, help="round depths to this step (0 = no rounding)")
    ap.add_argument("--keep-blank", action="store_true", help="also output intervals where the column is empty")
    ap.add_argument("--legend-method", choices=LEGEND_METHODS,
                    help=f"legend parser (default: LITHO_LEGEND_METHOD or {DEFAULT_LEGEND_METHOD})")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)

    result = extract(a.image, a.annotation, verbose=a.verbose, round_depth=a.round_depth, keep_blank=a.keep_blank,
                     legend_method_name=a.legend_method)
    text = json.dumps(result, indent=2)
    if a.output:
        Path(a.output).write_text(text)
    else:
        print(text)
    if a.csv:
        write_csv(result, a.csv)
    if a.preview:
        write_preview(a.image, result, a.preview)


if __name__ == "__main__":
    main()
