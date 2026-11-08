"""
Two more legend parsers that group a swatch with the text close to it. Both start from the same
swatch candidates and layout as the grid method (legend_grid.prepare), and read the text with ONE
OCR pass over the legend with the detected swatches erased (so a pattern is never read as letters).

boxes  - "what is close together belongs together". Grow every ink pixel (swatches and text) by a
         rectangle of kx x ky px; touching pieces become one box. Try a range of sizes and keep the
         one that gives the most boxes holding exactly one swatch plus text. A box with several
         swatches is split by giving each text line to the nearest swatch.

xycut  - split the legend into non-overlapping rectangles along empty lines (rows or columns of
         pure background), recursively, always taking the widest empty gap -- but only gaps that
         leave at least one swatch on both sides, so a swatch is never cut away from its own label.
         Stops when a rectangle holds one swatch.

Both return the same entries as extract.parse_legend, plus a `debug` dict for the review page.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage

from .legend_grid import _join_lines, _ocr_region, make_entry, prepare
from . import extract as X


# ----------------------------------------------------------------------------------------------
# shared helpers
# ----------------------------------------------------------------------------------------------


def _clean_ocr(P):
    """OCR the legend once with every detected swatch painted over with the background colour.
    Returns [(box, text)] in crop pixels."""
    arr = P["arr"].copy()
    for b in P["sw"]:
        arr[max(0, b[1] - 1) : b[3] + 1, max(0, b[0] - 1) : b[2] + 1] = P["bg"]
    H, W = arr.shape[:2]
    up = 2 if P["th"] < 20 else 1
    if up > 1:
        from PIL import Image
        arr = np.asarray(Image.fromarray(arr).resize((W * up, H * up), Image.Resampling.LANCZOS))
    out = []
    import re
    for x0, y0, x1, y1, t, s in X.ocr(arr):
        if s >= 0.6 and re.search(r"[A-Za-z]{2,}", t.strip()):
            out.append(([x0 / up, y0 / up, x1 / up, y1 / up], t.strip()))
    return out


def recover_symbols(P, pieces):
    """Swatches the blob step missed (small symbols, a pattern box cut up by OCR): for every text
    line, look left of it (right layout) for ink that is not text and not an already known swatch.
    Adds them to P["sw"], drops OCR text lying inside any swatch (letters drawn in a symbol box).
    Returns (recovered boxes, remaining pieces)."""
    if P["layout"] != "right":
        return [], pieces
    th, wm, H, W = P["th"], P["wm"], P["H"], P["W"]
    known = list(P["sw"])
    recovered = []

    # 1. a pattern OCR'd together with its label ("CCCCCCCEMENT"): the text line starts inside a
    #    swatch column and runs past it. Cut it at the column edge: left = swatch, right = label.
    fixed = []
    for b, t in pieces:
        col = [k for k in known if k[0] - 0.3 * wm <= b[0] <= k[0] + 0.5 * (k[2] - k[0])
               and not (k[1] - 0.3 * th <= (b[1] + b[3]) / 2 <= k[3] + 0.3 * th)]
        if len(col) >= 2:
            cx1 = float(np.median([k[2] for k in col]))
            if b[2] > cx1 + th:
                x0, y0, x1, y1 = int(cx1) + 2, int(max(0, b[1] - 2)), int(min(W, b[2] + 3)), int(min(H, b[3] + 2))
                sub = _ocr_region(P["arr"][y0:y1, x0:x1], P["bg"], up=2 if th < 20 else 1)
                nb = [int(b[0]), int(b[1]), int(cx1), int(b[3])]
                known.append(nb)
                recovered.append(nb)
                for sb, st in sub:
                    fixed.append(([sb[0] + x0, sb[1] + y0, sb[2] + x0, sb[3] + y0], st))
                continue
        fixed.append((b, t))
    pieces = fixed

    # 2. small symbols left of a label
    occ = P["ink_raw"] & ~P["rules"]
    for b, _ in pieces:
        occ[max(0, int(b[1]) - 1) : int(b[3]) + 2, max(0, int(b[0]) - 1) : int(b[2]) + 2] = False
    for k in known:  # a neighbouring row's swatch reaching into the search window is not a symbol
        occ[max(0, k[1] - 1) : k[3] + 1, max(0, k[0] - 1) : k[2] + 1] = False

    def overlaps(a, b):
        return min(a[2], b[2]) - max(a[0], b[0]) > 0 and min(a[3], b[3]) - max(a[1], b[1]) > 0
    for b, t in sorted(pieces, key=lambda p: p[0][1]):
        cy = (b[1] + b[3]) / 2
        if any(k[1] - 0.3 * th <= cy <= k[3] + 0.3 * th and k[2] <= b[0] + 3 and b[0] - k[2] < 3 * th for k in known):
            continue  # already has a swatch on its left
        y0, y1 = int(max(0, b[1] - 0.3 * th)), int(min(H, b[3] + 0.3 * th))
        xa, xb = int(max(0, b[0] - 1.6 * wm)), int(max(0, b[0] - 1))
        if xb - xa < 3:
            continue
        cols = occ[y0:y1, xa:xb].any(axis=0)
        # walk left from the text: allow a gap of up to 2 text heights, then take the ink run
        # until an empty stretch of half a text height
        x = len(cols) - 1
        gap = 0
        while x >= 0 and not cols[x] and gap < 2 * th:
            x -= 1
            gap += 1
        if x < 0 or not cols[x]:
            continue
        right_edge, empty = x, 0
        while x >= 0 and empty < max(3, 0.5 * th):
            empty = 0 if cols[x] else empty + 1
            x -= 1
        left_edge = x + 1 + empty
        seg = occ[y0:y1, xa + left_edge : xa + right_edge + 1]
        ys = np.flatnonzero(seg.any(axis=1))
        if not len(ys):
            continue
        nb = [xa + left_edge, y0 + int(ys[0]), xa + right_edge + 1, y0 + int(ys[-1]) + 1]
        if nb[2] - nb[0] < 3 or nb[3] - nb[1] < 3 or seg.mean() < 0.03:
            continue
        if any(overlaps(nb, k) for k in known) or any(overlaps(nb, q[0]) for q in pieces if q[0] is not b):
            continue
        known.append(nb)
        recovered.append(nb)
    P["sw"] = known
    keep = [(b, t) for b, t in pieces
            if not any(k[0] <= (b[0] + b[2]) / 2 <= k[2] and k[1] <= (b[1] + b[3]) / 2 <= k[3] for k in known)]
    return recovered, keep


def _center(b):
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def _label_for(P, swatch, pieces):
    """Pick the text lines that belong to `swatch` out of `pieces` (all inside the swatch's own
    region). Right layout: the line(s) on the swatch's row plus lines directly below them;
    below layout: lines under the swatch. Returns (used, unused)."""
    th = P["th"]
    x0, y0, x1, y1 = swatch
    used, unused = [], []
    for b, t in sorted(pieces, key=lambda p: p[0][1]):
        cy = (b[1] + b[3]) / 2
        if P["layout"] == "right":
            on_row = y0 - 0.5 * th <= cy <= y1 + 0.5 * th and b[0] >= x0 + 0.5 * (x1 - x0)
            cont = used and 0 <= b[1] - used[-1][0][3] < 0.8 * th and abs(b[0] - used[0][0][0]) < 2 * th
            (used if on_row or cont else unused).append((b, t))
        else:
            (used if b[1] >= y1 - 0.3 * th else unused).append((b, t))
    return used, unused


def _ser(pieces):
    return [{"box": [float(v) for v in b], "text": t} for b, t in pieces]


def _base_debug(P, box, method):
    return {"method": method, "crop_box": [int(v) for v in box], "crop_size": [int(P["W"]), int(P["H"])],
            "layout": P["layout"], "swatch_size": [round(P["wm"], 1), round(P["hm"], 1)],
            "words": [{"box": [float(v) for v in w["box"]], "text": w["text"]} for w in P["words"]],
            "swatches": [[int(v) for v in b] for b in P["sw"]],
            "rejected": [{"box": [int(v) for v in b], "why": "not swatch-sized"} for b in P["rejected"]]}


# ----------------------------------------------------------------------------------------------
# boxes: proximity grouping
# ----------------------------------------------------------------------------------------------


def _grow(mask, kx, ky):
    """Binary dilation by a kx x ky rectangle (separable, fast)."""
    m = mask.astype(np.uint8)
    if kx > 1:
        m = ndimage.maximum_filter1d(m, kx, axis=1)
    if ky > 1:
        m = ndimage.maximum_filter1d(m, ky, axis=0)
    return m.astype(bool)


def parse_legend_boxes(img, box, verbose=False, debug: dict | None = None):
    P = prepare(img, box)
    if P is None:
        return []
    pieces = _clean_ocr(P)
    recovered, pieces = recover_symbols(P, pieces)
    th, sw = P["th"], P["sw"]
    occ = P["ink_raw"] & ~P["rules"]

    centers = np.array([_center(b) for b in sw])
    pc = np.array([_center(b) for b, _ in pieces]) if pieces else np.zeros((0, 2))

    def evaluate(kx, ky):
        lab, n = ndimage.label(_grow(occ, kx, ky))
        sl = ndimage.find_objects(lab)
        s_lab = lab[centers[:, 1].astype(int), centers[:, 0].astype(int)] if len(centers) else np.array([])
        p_lab = lab[pc[:, 1].astype(int).clip(0, P["H"] - 1), pc[:, 0].astype(int).clip(0, P["W"] - 1)] if len(pc) else np.array([])
        good = bad = 0
        for k in range(1, n + 1):
            ns, npc = int((s_lab == k).sum()), int((p_lab == k).sum())
            if ns == 1 and npc >= 1:
                good += 1
            elif ns >= 2:
                bad += ns - 1
        return good - 0.7 * bad, good, bad, lab, sl

    # kernel sizes in units of the text height: horizontal gap swatch->label, vertical gap label lines
    kxs = sorted({max(2, int(round(f * th))) for f in (0.3, 0.6, 1.0, 1.5, 2.2, 3.0, 4.5)})
    kys = sorted({max(1, int(round(f * th))) for f in (0.1, 0.25, 0.5, 0.9, 1.4, 2.2, 3.0, 4.0)})
    tried, best = [], None
    for kx in kxs:
        for ky in kys:
            score, good, bad, lab, sl = evaluate(kx, ky)
            tried.append({"kx": kx, "ky": ky, "score": round(score, 1), "good": good, "bad": bad})
            # ties: prefer the smaller kernel (less chance of swallowing a neighbour)
            if best is None or score > best[0] + 1e-9:
                best = (score, kx, ky, lab, sl)
    _, kx, ky, lab, sl = best

    entries, cells, boxes, unassigned = [], [], [], []
    s_lab = [int(lab[int(c[1]), int(c[0])]) for c in centers]
    p_lab = [int(lab[min(P["H"] - 1, int(c[1])), min(P["W"] - 1, int(c[0]))]) for c in pc]
    for k, s_ in enumerate(sl, start=1):
        if s_ is None:
            continue
        bbox = [s_[1].start, s_[0].start, s_[1].stop, s_[0].stop]
        my_sw = [sw[i] for i, l in enumerate(s_lab) if l == k]
        my_p = [pieces[i] for i, l in enumerate(p_lab) if l == k]
        status = "ok" if len(my_sw) == 1 and my_p else "several swatches" if len(my_sw) > 1 else \
            "no text" if my_sw else "text only" if my_p else "ink only"
        if status != "ink only":
            boxes.append({"box": [int(v) for v in bbox], "status": status})
        if not my_sw:
            unassigned += my_p
            continue
        # give every text piece to its nearest swatch in the box (on the same row for "right")
        groups = {i: [] for i in range(len(my_sw))}
        for b, t in my_p:
            cx, cy = _center(b)
            def d(s):
                sx, sy = _center(s)
                return abs(cy - sy) * 3 + max(0, b[0] - s[2]) if P["layout"] == "right" else abs(cx - sx) + max(0, b[1] - s[3]) * 0.5
            groups[min(range(len(my_sw)), key=lambda i: d(my_sw[i]))].append((b, t))
        for i, s in enumerate(my_sw):
            used, unused = _label_for(P, s, groups[i])
            unassigned += unused
            raw = _join_lines(used)
            cell = {"cell": [float(v) for v in bbox], "swatch": [int(v) for v in s], "pieces": _ser(used), "raw_text": raw}
            if not raw:
                cell["skip"] = "no text"
            else:
                e = make_entry(P, s, raw)
                if e:
                    entries.append(e)
            cells.append(cell)

    if debug is not None:
        debug.update(_base_debug(P, box, "boxes"))
        debug.update({"restored": recovered, "kernel": [kx, ky], "kernels_tried": sorted(tried, key=lambda t: -t["score"])[:10],
                      "boxes": boxes, "cells": cells, "unassigned": _ser(unassigned)})
    return entries


# ----------------------------------------------------------------------------------------------
# xycut: recursive whitespace cuts
# ----------------------------------------------------------------------------------------------


def _gaps(profile, lo, min_len=2):
    """Runs of zeros in a 1-D profile, as (start, end) in absolute coordinates, not touching the ends."""
    z = np.r_[False, profile == 0, False]
    d = np.diff(z.astype(np.int8))
    starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    return [(lo + a, lo + b) for a, b in zip(starts, ends) if b - a >= min_len and a > 0 and b < len(profile)]


def parse_legend_xycut(img, box, verbose=False, debug: dict | None = None):
    P = prepare(img, box)
    if P is None:
        return []
    pieces = _clean_ocr(P)
    recovered, pieces = recover_symbols(P, pieces)
    sw = P["sw"]
    occ = P["ink_raw"] & ~P["rules"]
    centers = [_center(b) for b in sw]
    cuts, leaves = [], []

    def split(x0, y0, x1, y1, idx, depth=0):
        if len(idx) <= 1 or depth > 60:
            leaves.append(([x0, y0, x1, y1], idx))
            return
        region = occ[y0:y1, x0:x1]
        best = None  # (gap width, axis, position)
        for axis, prof, lo in (("x", region.sum(axis=0), x0), ("y", region.sum(axis=1), y0)):
            for a, b in _gaps(prof, lo):
                pos = (a + b) / 2
                k = 0 if axis == "x" else 1
                n_lo = sum(centers[i][k] < pos for i in idx)
                if 0 < n_lo < len(idx) and (best is None or b - a > best[0]):
                    best = (b - a, axis, pos, False)
        if best is None:
            # no clean gap separates the swatches (e.g. touching rows): cut halfway between the two
            # swatches that are furthest apart along the better-separated axis
            for k, axis in ((0, "x"), (1, "y")):
                v = sorted(centers[i][k] for i in idx)
                gaps = np.diff(v)
                j = int(np.argmax(gaps))
                if best is None or gaps[j] > best[0]:
                    best = (float(gaps[j]), axis, (v[j] + v[j + 1]) / 2, True)
        _, axis, pos, forced = best
        pos = int(round(pos))
        k = 0 if axis == "x" else 1
        lo_idx = [i for i in idx if centers[i][k] < pos]
        hi_idx = [i for i in idx if centers[i][k] >= pos]
        if axis == "x":
            cuts.append({"line": [pos, y0, pos, y1], "forced": forced})
            split(x0, y0, pos, y1, lo_idx, depth + 1)
            split(pos, y0, x1, y1, hi_idx, depth + 1)
        else:
            cuts.append({"line": [x0, pos, x1, pos], "forced": forced})
            split(x0, y0, x1, pos, lo_idx, depth + 1)
            split(x0, pos, x1, y1, hi_idx, depth + 1)

    split(0, 0, P["W"], P["H"], list(range(len(sw))))

    entries, cells, unassigned = [], [], []
    for (x0, y0, x1, y1), idx in leaves:
        mine = [(b, t) for b, t in pieces if x0 <= _center(b)[0] < x1 and y0 <= _center(b)[1] < y1]
        if not idx:
            unassigned += mine
            continue
        s = sw[idx[0]]
        used, unused = _label_for(P, s, mine)
        unassigned += unused
        raw = _join_lines(used)
        cell = {"cell": [float(x0), float(y0), float(x1), float(y1)], "swatch": [int(v) for v in s],
                "pieces": _ser(used), "raw_text": raw}
        if not raw:
            cell["skip"] = "no text"
        else:
            e = make_entry(P, s, raw)
            if e:
                entries.append(e)
        cells.append(cell)

    if debug is not None:
        debug.update(_base_debug(P, box, "xycut"))
        debug.update({"restored": recovered, "cuts": cuts, "cells": cells, "unassigned": _ser(unassigned)})
    return entries
