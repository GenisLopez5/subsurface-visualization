"""
Legend parsing, "grid" method: split the legend into non-overlapping cells, one per entry, each holding
a swatch and its label.

  1. Find swatch candidates (ink blobs, with text masked out, as in extract.parse_legend) and keep
     the ones whose size is close to the typical swatch size (drops letter leftovers, frames, titles).
  2. Layout: are labels to the right of the swatches or below them?
  3. Columns: cluster the swatches by left edge. Swatches hidden by OCR (a pattern read as text,
     e.g. "CCCCCCCEMENT") are restored where an OCR word covers a column's swatch strip.
  4. Cells: inside a column, rows split between consecutive swatches; columns split between
     neighbouring swatch columns. Cells never overlap, so text can't drift into a neighbour's label.
  5. OCR each cell's text area on its own (the swatch is not in the crop) and fingerprint the swatch.

Returns the same entries as extract.parse_legend, so it can be swapped in.
"""
from __future__ import annotations

import difflib
import re

import numpy as np
from PIL import Image
from scipy import ndimage

from . import extract as X


# adjectives that the base vocabulary lacks, so they got "corrected" into nouns (SHALY -> SHALE)
VOCAB = X.LITHOLOGY_VOCAB + """shaly dolomitic calcitic sandy clayey limy cherty pebbly sample available colored
limestone mudstone fine medium very gas oil good poor fair""".split()


def clean_label(text: str) -> str:
    """Like extract.clean_label, but only corrects alphabetic tokens, so "Clay-M" stays "Clay-M"
    (the original turns it into "Clay") and "Sand-SS" stays "Sand-SS"."""
    t = re.sub(r"\b([A-Za-z]) (?=[a-z])", r"\1", text)
    t = re.sub(r"\s+", " ", t).strip(" .:-_|")

    def fix(m):
        w = m.group(0)
        if len(w) <= 4:  # short words are too easy to "correct" wrongly (Core -> Coal)
            return w
        hit = difflib.get_close_matches(w.lower(), VOCAB, n=1, cutoff=0.75)
        if not hit or hit[0] == w.lower():
            return w
        f = hit[0]
        return f.upper() if w.isupper() else f.capitalize() if w[:1].isupper() else f

    return re.sub(r"[A-Za-z]+", fix, t)


def _cluster(values, gap):
    """1-D clustering: sorted values split where consecutive ones differ by more than gap."""
    order = np.argsort(values)
    groups, cur = [], [order[0]]
    for a, b in zip(order[:-1], order[1:]):
        if values[b] - values[a] > gap:
            groups.append(cur)
            cur = []
        cur.append(b)
    groups.append(cur)
    return groups


def _ocr_region(arr, bg, up=1):
    """OCR a crop: pad with background, optionally upscale. Returns [(box, text)] in crop px."""
    h, w = arr.shape[:2]
    if h < 4 or w < 4:
        return []
    pad = 12
    canvas = np.empty((h + 2 * pad, w + 2 * pad, 3), np.uint8)
    canvas[:] = bg
    canvas[pad : pad + h, pad : pad + w] = arr
    if up > 1:
        canvas = np.asarray(Image.fromarray(canvas).resize((canvas.shape[1] * up, canvas.shape[0] * up), Image.Resampling.LANCZOS))
    out = []
    for x0, y0, x1, y1, t, s in X.ocr(canvas):
        if s >= 0.6 and re.search(r"[A-Za-z]{2,}", t.strip()):
            out.append(([x0 / up - pad, y0 / up - pad, x1 / up - pad, y1 / up - pad], t.strip()))
    return out


def _join_lines(items):
    """Text pieces -> one label, in reading order (lines top to bottom, left to right)."""
    if not items:
        return ""
    items = sorted(items, key=lambda it: (it[0][1] + it[0][3]) / 2)
    lines, cur = [], [items[0]]
    for it in items[1:]:
        prev = cur[-1][0]
        if (it[0][1] + it[0][3]) / 2 - (prev[1] + prev[3]) / 2 < 0.6 * (prev[3] - prev[1]):
            cur.append(it)
        else:
            lines.append(cur)
            cur = [it]
    lines.append(cur)
    return " ".join(" ".join(t for _, t in sorted(line, key=lambda it: it[0][0])) for line in lines)


def parse_legend_grid(img, box, verbose=False, debug: dict | None = None):
    lx0, ly0, _, _ = box
    arr = X.crop_rgb(img, box)
    H, W = arr.shape[:2]
    up = 2 if min(H, W) < 700 else 1
    big = np.asarray(Image.fromarray(arr).resize((W * up, H * up), Image.Resampling.LANCZOS)) if up > 1 else arr
    words = []
    for x0, y0, x1, y1, t, s in X.ocr(big):
        if s >= 0.5 and re.search(r"[A-Za-z]{2,}", t):
            words.append({"box": [x0 / up, y0 / up, x1 / up, y1 / up], "text": t})

    flat = arr.reshape(-1, 3)
    vals, counts = np.unique(flat[:: max(1, len(flat) // 200000)], axis=0, return_counts=True)
    bg = vals[counts.argmax()].astype(int)
    ink = np.abs(arr.astype(int) - bg).max(axis=2) > 40
    for w in words:
        x0, y0, x1, y1 = w["box"]
        ink[max(0, int(y0) - 2) : int(y1) + 3, max(0, int(x0) - 2) : int(x1) + 3] = False
    # long rules (section separators, the outer frame) glue swatches together: remove them first
    L = max(60, W // 6)
    ink &= ~ndimage.binary_opening(ink, structure=np.ones((1, L), bool))
    Lv = max(60, H // 3)
    ink &= ~ndimage.binary_opening(ink, structure=np.ones((Lv, 1), bool))
    lab, _ = ndimage.label(ndimage.binary_dilation(ink, structure=np.ones((1, 7), bool)))
    th = float(np.median([w["box"][3] - w["box"][1] for w in words])) if words else 10.0
    min_side = max(6, 0.5 * th)
    blobs = []
    for sl in ndimage.find_objects(lab):
        y0, y1, x0, x1 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop
        if x1 - x0 >= min_side and y1 - y0 >= min_side and x1 - x0 <= 0.6 * W and y1 - y0 <= 0.6 * H:
            blobs.append([x0, y0, x1, y1])
    if not blobs:
        return []

    # 1. typical swatch size: the most common (width, height) among blobs, then keep blobs near it
    bw = np.array([b[2] - b[0] for b in blobs], float)
    bh = np.array([b[3] - b[1] for b in blobs], float)
    # weight by area so tiny letter leftovers don't win the vote
    wm = float(np.median(np.repeat(bw, 1 + (bw * bh / (bw * bh).max() * 5).astype(int))))
    hm = float(np.median(np.repeat(bh, 1 + (bw * bh / (bw * bh).max() * 5).astype(int))))
    keep = (bw > 0.55 * wm) & (bw < 1.6 * wm) & (bh > 0.5 * hm) & (bh < 1.8 * hm)
    sw = [blobs[i] for i in np.flatnonzero(keep)]

    def inside(a, b):  # a mostly inside b
        ov = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
        return a is not b and ov > 0.8 * (a[2] - a[0]) * (a[3] - a[1])
    sw = [a for a in sw if not any(inside(a, b) for b in sw)]  # a symbol drawn inside its frame
    rejected = [blobs[i] for i in np.flatnonzero(~keep)]

    # 2. layout: labels right of swatches, or below?
    right = below = 0
    for x0, y0, x1, y1 in sw:
        for w in words:
            tx0, ty0, tx1, ty1 = w["box"]
            if tx0 >= x0 + 0.6 * (x1 - x0) and tx0 - x1 < max(3 * th, 0.6 * wm) and min(y1, ty1) - max(y0, ty0) > 0.3 * min(y1 - y0, ty1 - ty0):
                right += 1
                break
            if ty0 >= y1 - 3 and ty0 - y1 < max(3 * th, 0.6 * hm) and x0 <= (tx0 + tx1) / 2 <= x1:
                below += 1
                break
    layout = "right" if right >= below else "below"

    # 3. columns: first group swatches by left edge, then merge groups whose x-ranges overlap and
    #    that never sit on the same row (e.g. pattern boxes above smaller, shifted symbol boxes)
    groups = []  # [x0, x1, [swatches]]
    xs0 = np.array([b[0] for b in sw], float)
    for g in _cluster(xs0, 0.25 * wm):
        bs = [sw[i] for i in g]
        groups.append([min(b[0] for b in bs), max(b[2] for b in bs), bs])

    def same_row(a, b):
        return min(a[3], b[3]) - max(a[1], b[1]) > 0.3 * min(a[3] - a[1], b[3] - b[1])
    merged = True
    while merged:
        merged = False
        groups.sort(key=lambda g: g[0])
        for i in range(len(groups) - 1):
            a, b = groups[i], groups[i + 1]
            if min(a[1], b[1]) - max(a[0], b[0]) > -0.1 * wm and not any(same_row(x, y) for x in a[2] for y in b[2]):
                groups[i] = [min(a[0], b[0]), max(a[1], b[1]), a[2] + b[2]]
                del groups[i + 1]
                merged = True
                break
    cols = []
    for _, _, bs in groups:
        cols.append({"x0": float(np.median([b[0] for b in bs])), "x1": float(np.median([b[2] for b in bs])),
                     "x1_min": float(min(b[2] for b in bs)), "sw": bs})
    cols.sort(key=lambda c: c["x0"])
    def labelled(b):
        return any(w["box"][0] >= b[0] + 0.6 * (b[2] - b[0]) and w["box"][0] - b[2] < max(3 * th, 0.6 * wm)
                   and min(b[3], w["box"][3]) - max(b[1], w["box"][1]) > 0 for w in words) if layout == "right" else True
    # a stray lone blob is not a column, unless it has a label right next to it
    cols = [c for c in cols if len(c["sw"]) >= 2 or len(cols) <= 2 or labelled(c["sw"][0])]

    # 4. text: OCR one strip per column (the text area between this column's swatches and the next
    #    column), then give every text line to the swatch on its row (right layout) or above it (below).
    ink_raw = np.abs(arr.astype(int) - bg).max(axis=2) > 40
    restored, cells = [], []
    ocr_up = 2 if th < 20 else 1
    for ci, c in enumerate(cols):
        if layout == "right":
            sx0 = int(c["x1_min"]) + 2  # the narrowest swatch's edge: wider ones are painted out below
            sx1 = int(min(b[0] for b in cols[ci + 1]["sw"])) - 3 if ci + 1 < len(cols) else W
        else:
            mid = (c["x0"] + c["x1"]) / 2
            sx0 = 0 if ci == 0 else int((mid + (cols[ci - 1]["x0"] + cols[ci - 1]["x1"]) / 2) / 2)
            sx1 = W if ci + 1 == len(cols) else int((mid + (cols[ci + 1]["x0"] + cols[ci + 1]["x1"]) / 2) / 2)
        strip_img = arr[:, sx0:sx1].copy()
        for b in c["sw"]:  # hide swatch parts that reach into the strip (their pattern isn't text)
            if b[2] > sx0:
                strip_img[max(0, b[1] - 1) : b[3] + 1, : max(0, b[2] + 1 - sx0)] = bg
        pieces = _ocr_region(strip_img, bg, up=ocr_up) if sx1 - sx0 > 4 else []
        pieces = [([b[0] + sx0, b[1], b[2] + sx0, b[3]], t) for b, t in pieces]
        pieces.sort(key=lambda p: p[0][1])
        bs = sorted(c["sw"], key=lambda b: b[1])

        if layout == "right":
            # a text line next to the swatch strip where no swatch was found, but the strip has ink:
            # the swatch was missed (masked as text, odd size, too small, ...) -> restore it
            for b_, t in pieces:
                cy = (b_[1] + b_[3]) / 2
                if any(s_[1] - 0.5 * th <= cy <= s_[3] + 0.5 * th for s_ in bs):
                    continue
                if b_[0] - c["x1"] > max(3 * th, 0.6 * wm):
                    continue
                # look for the symbol only at the height of this text line (rows can be tight)
                y0_, y1_ = int(max(0, b_[1] - 2)), int(min(H, b_[3] + 2))
                xa, xb = max(0, int(c["x0"]) - 3), max(0, int(min(b_[0], c["x1"] + 3)) - 1)
                strip = ink_raw[y0_:y1_, xa:xb]
                if not strip.size or strip.mean() < 0.04:
                    continue
                # ink that is part of OCR'd text (a title running across the column) is no swatch --
                # unless a real swatch sits on this row in another column (a pattern that OCR read as text)
                if ink[int(b_[1]) : int(b_[3]) + 1, xa:xb].mean() < 0.02 and not any(s_[1] <= cy <= s_[3] for s_ in sw):
                    continue
                ys, xs = np.nonzero(strip)
                nb = [int(xa + xs.min()), int(y0_ + ys.min()), int(xa + xs.max() + 1), int(y0_ + ys.max() + 1)]
                if nb[2] - nb[0] >= 3 and nb[3] - nb[1] >= 3 and not any(
                        min(nb[3], s_[3]) - max(nb[1], s_[1]) > 0.3 * min(nb[3] - nb[1], s_[3] - s_[1]) for s_ in bs):
                    bs.append(nb)
                    restored.append(nb)
            bs.sort(key=lambda b: b[1])

            # small symbols (e.g. a tiny black square for coal) are not swatch-sized, so the strip OCR
            # (which starts right of the swatch column) skips their label. Take the label from the
            # full-legend OCR and the symbol from the ink left of it.
            for w in words:
                wx0, wy0, wx1, wy1 = w["box"]
                cy = (wy0 + wy1) / 2
                if not (c["x0"] - 3 <= wx0 < c["x1"] + 2) or wx1 > sx1 + 3:
                    continue
                if any(pb[1] <= cy <= pb[3] for pb, _ in pieces) or any(s_[1] <= cy <= s_[3] for s_ in bs):
                    continue
                reg = ink_raw[int(wy0) : int(wy1) + 1, max(0, int(c["x0"]) - 3) : max(0, int(wx0) - 2)]
                ys, xs = np.nonzero(reg)
                if len(xs) < 6:
                    continue
                ox, oy = max(0, int(c["x0"]) - 3), int(wy0)
                nb = [int(ox + xs.min()), int(oy + ys.min()), int(ox + xs.max() + 1), int(oy + ys.max() + 1)]
                if nb[2] - nb[0] >= 3 and nb[3] - nb[1] >= 3:
                    bs.append(nb)
                    restored.append(nb)
                    pieces.append(([wx0, wy0, wx1, wy1], w["text"]))
            bs.sort(key=lambda b: b[1])
            pieces.sort(key=lambda p: p[0][1])

        col_cells = [{"col": ci, "swatch": [int(v) for v in b], "pieces": []} for b in bs]
        for b_, t in pieces:
            cy = (b_[1] + b_[3]) / 2
            target = None
            if layout == "right":
                hits = [k for k, cc in enumerate(col_cells)
                        if cc["swatch"][1] - 0.5 * th <= cy <= cc["swatch"][3] + 0.5 * th]
                if hits:
                    target = min(hits, key=lambda k: abs(cy - (col_cells[k]["swatch"][1] + col_cells[k]["swatch"][3]) / 2))
                else:  # continuation line right under a label (multi-line label), within the row band
                    for k, cc in enumerate(col_cells):
                        s_ = cc["swatch"]
                        band_end = ((s_[1] + s_[3]) / 2 + (col_cells[k + 1]["swatch"][1] + col_cells[k + 1]["swatch"][3]) / 2) / 2 \
                            if k + 1 < len(col_cells) else s_[3] + max(hm, th)
                        if cc["pieces"] and 0 <= b_[1] - cc["pieces"][-1][0][3] < 0.8 * th and cy < band_end:
                            target = k
            else:
                for k, cc in enumerate(col_cells):
                    nxt = col_cells[k + 1]["swatch"][1] if k + 1 < len(col_cells) else H
                    if cc["swatch"][3] - 0.3 * th <= cy < nxt and b_[1] - cc["swatch"][3] < 3 * th:
                        target = k
            if target is not None:
                col_cells[target]["pieces"].append((b_, t))
        for cc in col_cells:
            s_ = cc["swatch"]
            if layout == "right":
                cc["cell"] = [float(c["x0"]), float(s_[1]), float(sx1), float(s_[3])]
            else:
                cc["cell"] = [float(sx0), float(s_[1]), float(sx1), float(s_[3])]
            for b_, _ in cc["pieces"]:
                cc["cell"] = [min(cc["cell"][0], b_[0]), min(cc["cell"][1], b_[1]),
                              max(cc["cell"][2], b_[2]), max(cc["cell"][3], b_[3])]
        cells += col_cells

    # 5. entries: label from the cell's text, fingerprint from its swatch
    entries = []
    for cell in cells:
        raw = _join_lines(cell["pieces"])
        cell["raw_text"] = raw
        cell["cell"] = [float(v) for v in cell["cell"]]
        cell["pieces"] = [{"box": [float(v) for v in b_], "text": t} for b_, t in cell["pieces"]]
        if not raw:
            cell["skip"] = "no text"
            continue
        sbox = X.shrink(cell["swatch"])
        patch = arr[sbox[1] : sbox[3], sbox[0] : sbox[2]]
        if patch.size == 0:
            cell["skip"] = "empty swatch"
            continue
        feat = X.counts_to_features(X.row_counts(patch).sum(0), patch.shape[0] * patch.shape[1])
        if feat[4] < 0.005:
            feat[5:9] = 0.25
        s_ = cell["swatch"]
        entries.append({
            "label": clean_label(raw), "raw_text": raw,
            "swatch_bbox": [int(s_[0] + lx0), int(s_[1] + ly0), int(s_[2] + lx0), int(s_[3] + ly0)],
            "swatch_height": int(sbox[3] - sbox[1]), "feature": feat,
        })

    if debug is not None:
        debug.update({
            "method": "grid", "crop_box": [int(v) for v in box], "crop_size": [int(W), int(H)],
            "layout": layout, "swatch_size": [round(wm, 1), round(hm, 1)],
            "words": [{"box": [float(v) for v in w["box"]], "text": w["text"]} for w in words],
            "swatches": [[int(v) for v in b] for b in sw],
            "restored": restored,
            "rejected": [{"box": [int(v) for v in b], "why": "not swatch-sized"} for b in rejected],
            "columns": [{"x0": float(c["x0"]), "x1": float(c["x1"]), "n": len(c["sw"])} for c in cols],
            "cells": cells,
        })
    if verbose:
        import sys
        print(f"[legend/grid] layout={layout}, {len(cols)} columns, {len(cells)} cells, {len(entries)} entries", file=sys.stderr)
    return entries
