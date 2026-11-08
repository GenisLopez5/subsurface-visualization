"""
Clean-up of the intervals that extract.classify_column finds (also known as method "lithology2" in cv_approach_1/).
extract.extract() runs it by default (LITHO_REFINE=0 turns it off); the depth fit is not touched.

lithology/ cuts the column well, but names every interval on its own against the legend fingerprints. On black-and-white
logs the column is drawn denser and with thicker strokes than the legend swatches, so the fingerprints drift: the same
pattern gets two names (Halite / Siltstone flickering, Shale read as "Silty Sandstone" or "Limestone"), annotation boxes
drawn over the column get a lithology, and an empty column can be read as a pattern. lithology2 keeps lithology/'s
legend reader, boundaries and depth fit, and changes the naming:

 1. Shape fingerprint   every interval and every swatch is described by the shapes of its ink blobs, measured relative
                        to the stroke width (so it survives the legend/column scale difference): dots, horizontal
                        dashes, vertical bars, diagonal lines, hooks (halite's Г), brick networks, solid fill.
                        Colour logs keep lithology/'s colour fingerprint (the fill colour is the reliable cue there).
 2. Blank               an interval with (almost) no pattern ink is blank (the column is empty).
 3. One pattern, one    intervals that look alike (compared column to column, same drawing, so reliable) are grouped,
    name                and every group gets one name: the nearest swatch. No "?": near-ties are decided by
                        lithology/'s spacing/ink fingerprint.
 4. Noise               an interval that matches neither a group nor a swatch (an annotation box over the column, a
                        smudge) takes the name of its previous or next interval (the more similar one); if it has no
                        named neighbour, it stays blank.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import pdist, squareform

from dataclasses import dataclass

from . import extract as LX

# identical-looking swatches (Shale / Clay): the most common rock wins (same order as cv_pipeline.COMMONNESS)
COMMONNESS = [n.lower() for n in ("Shale", "Sandstone", "Sand and Sandstone", "Sand", "Siltstone", "Limestone",
                                   "Dolomite", "Clay", "Claystone", "Mudstone", "Coal", "Marl", "Chalk", "Anhydrite",
                                   "Halite", "Gypsum")]
LOOKALIKE = 0.15                                                          # shape distance below which two swatches look the same


@dataclass
class RefineConfig:
    """Defaults (cv_approach_1's lithology_pipeline.Config has the same fields and can be passed instead)."""
    lith2_group_dist: float = 0.45         # B/W: shape fingerprints closer than this (L1, 0-2) are one pattern -> one name
    lith2_noise_dist: float = 0.9          # B/W: farther than this from every swatch (and alone) -> noise
    lith2_group_dist_colour: float = 0.25  # colour logs: same on extract's colour fingerprint
    lith2_noise_dist_colour: float = 0.8
    lith2_blank_ink: float = 0.012         # B/W: less pattern ink than this -> blank
    lith2_split: bool = True               # B/W: split intervals where the dominant blob shape changes
    lith2_split_px: int = 0                # shortest split piece, px; 0 = median swatch height (8-40)
    lith2_tiebreak: float = 0.15           # B/W: weight of extract's spacing/ink fingerprint in near-ties
    lith2_weak_dist: float = 9.0           # B/W: a group of only short pieces farther than this from its swatch = boundary mix -> neighbour; 9 = off (0.3 also ate real thin siltstone beds)


CATS = ("dot", "hline", "vline", "dline", "hook", "network", "solid")
NOISE = "__noise__"


# ======================================================================================================================
# Shape fingerprint
# ======================================================================================================================
def ink_mask(img, box) -> np.ndarray:
    a = LX.crop_rgb(img, box).astype(np.float32) / 255.0
    return (a @ LX.LUM) < 0.45


def shape_fingerprint(m: np.ndarray, rows: bool = False) -> dict:
    """Ink mask of a patch -> {hist: share of pattern ink per CATS, ink: pattern ink per pixel, boxed: share of the
    patch's rows covered by a rectangular frame (an annotation box drawn over the column)}."""
    H, W = m.shape
    empty = dict(hist=np.zeros(len(CATS)), ink=0.0, boxed=0.0)
    if H < 3 or W < 3 or not m.any():
        return empty
    lab, n = ndimage.label(m, np.ones((3, 3)))
    idx = np.arange(1, n + 1)
    cnt = np.bincount(lab.ravel(), minlength=n + 1)[1:].astype(float)
    yy, xx = np.indices(m.shape)
    my, mx = ndimage.mean(yy, lab, idx), ndimage.mean(xx, lab, idx)
    syy = ndimage.mean(yy * yy, lab, idx) - my * my
    sxx = ndimage.mean(xx * xx, lab, idx) - mx * mx
    sxy = ndimage.mean(xx * yy, lab, idx) - mx * my
    tr, det = sxx + syy, sxx * syy - sxy ** 2
    disc = np.sqrt(np.maximum(tr * tr / 4 - det, 0))
    length = np.sqrt(12 * (tr / 2 + disc) + 1)                      # length / width of the equivalent bar
    width = np.sqrt(12 * np.maximum(tr / 2 - disc, 0) + 1)
    ang = np.abs(np.degrees(0.5 * np.arctan2(2 * sxy, sxx - syy)))   # 0 = horizontal, 90 = vertical
    sl = ndimage.find_objects(lab)
    x0 = np.array([s[1].start for s in sl]); x1 = np.array([s[1].stop for s in sl])
    y0 = np.array([s[0].start for s in sl]); y1 = np.array([s[0].stop for s in sl])
    bw, bh = x1 - x0, y1 - y0
    fill = cnt / (bw * bh)

    near_l, near_r = x0 < 0.15 * W, x1 > 0.85 * W
    frame = (near_l | near_r) & (bh > 0.8 * H) & ((bw < 0.15 * W)                 # column / swatch border, also when
                                                   | ((near_l ^ near_r) & (fill < 0.1)))  # joined to a bit of a ruled line
    grid = (bw > 0.9 * W) & (bw >= 8 * bh)                                      # ruled line across (grid, border)
    big = (bw > 0.6 * W) & ~frame & ~grid
    # annotation box: a big blob whose ink lies on its bounding rectangle (bricks have ink inside too)
    boxed_rows = np.zeros(H, bool)
    for i in np.flatnonzero(big & (fill < 0.5) & (bh > 8)):
        comp = lab[sl[i]] == i + 1
        t = max(2, int(0.08 * min(bw[i], bh[i])))
        inner = comp[t:-t, t:-t].sum() if comp.shape[0] > 2 * t and comp.shape[1] > 2 * t else 0
        if inner < 0.15 * cnt[i]:
            boxed_rows[y0[i]:y1[i]] = True
    keep = ~(frame | grid)
    left = frame & (bw < 0.15 * W) & near_l                                  # blobs outside the frame lines (depth
    right = frame & (bw < 0.15 * W) & near_r                                 # ticks, neighbouring track) are not pattern
    if left.any():
        keep &= x0 >= x1[left].max()
    if right.any():
        keep &= x1 <= x0[right].min()
    elong = length / np.maximum(width, 1)
    el = keep & (elong > 2.5)
    stroke = float(np.median(width[el])) if el.sum() >= 3 else float(np.median(width[keep])) if keep.any() else 2.0

    cat = np.full(n, 4)                                                      # default: hook (Г, L, irregular)
    cat[big & (fill > 0.6)] = 6
    cat[big & (fill <= 0.6)] = 5
    small = ~big
    line = small & (elong >= 2.5)
    cat[line & (ang < 25) & (fill > 0.3)] = 1
    cat[line & (ang > 65) & (fill > 0.3)] = 2
    cat[line & (ang >= 25) & (ang <= 65)] = 3
    cat[small & (elong < 2.5) & (np.maximum(bw, bh) <= max(4 * stroke, 0.05 * W, 6))] = 0
    h = np.bincount(cat[keep], weights=cnt[keep], minlength=len(CATS))
    small_ink = cnt[keep & small].sum() / (H * W)                            # ink of the pattern itself, no frames/boxes
    out = dict(hist=h / h.sum() if h.sum() else h, ink=float(small_ink), boxed=float(boxed_rows.mean()))
    if rows:                                                                 # ink per row and category (for splitting)
        prof = np.zeros((H, len(CATS)))
        k = keep & small
        np.add.at(prof, (np.clip(np.round(my[k]).astype(int), 0, H - 1), cat[k]), cnt[k])
        out["profile"] = prof
    return out


def split_points(profile: np.ndarray, min_len: int) -> list:
    """Rows where the dominant blob type changes for at least min_len rows (two patterns lithology/ left in one run,
    e.g. thin shale / siltstone beds). Returns cut rows relative to the run's top."""
    H = len(profile)
    if H < 2 * min_len:
        return []
    sm = ndimage.uniform_filter1d(profile, size=min_len, axis=0, mode="nearest")
    lab = np.where(sm.sum(1) > 0, sm.argmax(1), -1)
    for i in range(1, H):                                                    # rows without ink: carry the label on
        if lab[i] < 0:
            lab[i] = lab[i - 1]
    edges = np.flatnonzero(np.diff(lab)) + 1
    starts, ends = np.r_[0, edges], np.r_[edges, H]
    segs = [[int(lab[a]), int(a), int(b)] for a, b in zip(starts, ends)]
    changed = True
    while changed and len(segs) > 1:                                         # absorb pieces thinner than min_len
        changed = False
        i = min(range(len(segs)), key=lambda j: segs[j][2] - segs[j][1])
        if segs[i][2] - segs[i][1] < min_len:
            j = i - 1 if i == len(segs) - 1 or (i > 0 and segs[i - 1][2] - segs[i - 1][1] >= segs[i + 1][2] - segs[i + 1][1]) else i + 1
            a, b = min(segs[i][1], segs[j][1]), max(segs[i][2], segs[j][2])
            segs[min(i, j)] = [segs[j][0], a, b]
            del segs[max(i, j)]
            k = 1                                                             # re-merge equal neighbours
            while k < len(segs):
                if segs[k][0] == segs[k - 1][0]:
                    segs[k - 1][2] = segs[k][2]
                    del segs[k]
                else:
                    k += 1
            changed = True
    return [a for _, a, _ in segs[1:]]


def hist_distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(a - b).sum())                                     # L1 between distributions, 0..2


# ======================================================================================================================
# Refine lithology/'s runs
# ======================================================================================================================
def refine(image, column_box, entries: list, runs: list, colour: bool = False, cfg=None) -> tuple[list, dict]:
    """runs = lithology/'s [(name, top_y, bottom_y, conf)] -> cleaned runs (same format) + info for the record."""
    cfg = cfg or RefineConfig()
    x0, y0, x1, y1 = column_box
    cx0, _, cx1, _ = LX.shrink((x0, y0, x1, y1), frac=0.06, min_px=2)
    swatches = [e for e in entries if not e.get("ignored")]
    seen, refs = set(), []
    for e in swatches:                                                     # one reference per name
        if e["label"] in seen:
            continue
        seen.add(e["label"])
        sb = LX.shrink(tuple(e["swatch_bbox"]), frac=0.08, min_px=3)
        refs.append(dict(name=e["label"], lx=np.asarray(e["feature"], np.float32),
                         shape=shape_fingerprint(ink_mask(image, sb))["hist"]))
    info = dict(n_input=len(runs), blank=0, noise=0, groups=0, renamed=0)
    if not refs or not runs:
        return runs, info

    # 1. describe every run; black-and-white: split runs that hold two patterns (dominant blob type changes)
    hs = [e["swatch_height"] for e in swatches if e.get("swatch_height")]
    min_len = cfg.lith2_split_px or int(np.clip(np.median(hs) if hs else 20, 8, 40))
    pieces = []
    for name, top, bottom, conf in runs:
        top, bottom = int(top), int(bottom)
        if bottom - top < 2:
            continue
        cuts = []
        if not colour and name != LX.BLANK and cfg.lith2_split:
            prof = shape_fingerprint(ink_mask(image, (cx0, top, cx1, bottom)), rows=True).get("profile")
            if prof is not None:
                cuts = [top + c for c in split_points(prof, min_len)]
        edges = [top] + cuts + [bottom]
        pieces += [(name, a, b, conf) for a, b in zip(edges[:-1], edges[1:])]
    info["split"] = len(pieces) - len(runs)
    segs = []
    for name, top, bottom, conf in pieces:
        box = (cx0, top, cx1, bottom)
        sf = shape_fingerprint(ink_mask(image, box))
        lx = LX.counts_to_features(LX.row_counts(LX.crop_rgb(image, box)).sum(0), (bottom - top) * (cx1 - cx0))
        segs.append(dict(old=name, top=top, bottom=bottom, conf=conf, lx=lx, **sf))

    # colour log? lithology_pipeline only looks at the column's first 4000 rows (often empty): look at every interval
    if not colour and segs:
        fills = np.stack([s["lx"][0:3] for s in segs])
        spread = fills.max(1) - fills.min(1)                                 # 0 = grey, ~1 = saturated colour
        rows = np.array([s["bottom"] - s["top"] for s in segs], float)
        if rows[spread > 0.15].sum() > 0.05 * rows.sum():
            colour = True
    info["colour"] = bool(colour)

    # 2. blank: lithology/ says blank, or (black-and-white) no pattern ink
    for s in segs:
        s["blank"] = s["old"] == LX.BLANK or (not colour and s["ink"] < cfg.lith2_blank_ink)
        s["noise"] = (not s["blank"]) and s["boxed"] > 0.5                  # mostly an annotation box
    info["blank"] = sum(s["blank"] for s in segs)

    if colour:
        dist = lambda a, b: float(LX.feature_distance(a["lx"][None], b["lx"])[0])
        group_thr, noise_thr = cfg.lith2_group_dist_colour, cfg.lith2_noise_dist_colour
    else:
        dist = lambda a, b: hist_distance(a["hist"], b["hist"])
        group_thr, noise_thr = cfg.lith2_group_dist, cfg.lith2_noise_dist

    def ref_dists(feat_shape, feat_lx):
        """Distance to every legend reference. B/W: shape + a small spacing/ink term (decides near-ties)."""
        lxd = np.array([float(LX.feature_distance(feat_lx[None], r["lx"])[0]) for r in refs])
        if colour:
            return lxd
        sd = np.array([hist_distance(feat_shape, r["shape"]) for r in refs])
        return sd + cfg.lith2_tiebreak * lxd / max(float(lxd.max()), 1e-6)

    # 3. group look-alike runs (column vs column), name each group once
    live = [s for s in segs if not s["blank"] and not s["noise"]]
    if live:
        if len(live) == 1:
            labels = np.array([1])
        else:
            if colour:
                F = np.stack([s["lx"] for s in live])
                D = np.stack([LX.feature_distance(F, f) for f in F])
            else:
                Hh = np.stack([s["hist"] for s in live])
                D = squareform(pdist(Hh, "cityblock"))
            D = (D + D.T) / 2
            np.fill_diagonal(D, 0)
            labels = fcluster(linkage(squareform(D, checks=False), "average"), group_thr, criterion="distance")
        groups = {}
        for s, g in zip(live, labels):
            groups.setdefault(int(g), []).append(s)
        info["groups"] = len(groups)
        centres = []
        for g, members in groups.items():
            w = np.array([m["bottom"] - m["top"] for m in members], float)
            c_shape = np.average([m["hist"] for m in members], axis=0, weights=w)
            c_lx = np.average([m["lx"] for m in members], axis=0, weights=w).astype(np.float32)
            d = ref_dists(c_shape, c_lx)
            order = np.argsort(d)
            best = int(order[0])
            if not colour:                                                # look-alike swatches: commonness decides
                twins = [i for i in range(len(refs)) if d[i] <= d[best] + LOOKALIKE
                         and hist_distance(refs[i]["shape"], refs[best]["shape"]) < LOOKALIKE]
                rank = lambda i: COMMONNESS.index(refs[i]["name"].lower()) if refs[i]["name"].lower() in COMMONNESS \
                    else len(COMMONNESS)
                best = min(twins, key=lambda i: (rank(i), d[i]))
            conf = float((d[order[1]] - d[best]) / max(d[order[1]], 1e-6)) if len(d) > 1 else 1.0
            centres.append(dict(hist=c_shape, lx=c_lx, name=refs[best]["name"]))
            info.setdefault("group_detail", []).append(dict(
                name=refs[best]["name"], n_intervals=len(members), rows=int(w.sum()),
                shape={c: round(float(v), 2) for c, v in zip(CATS, c_shape) if v >= 0.01},
                nearest={refs[i]["name"]: round(float(d[i]), 3) for i in order[:3]}))
            for m in members:
                m["new"] = refs[best]["name"]
                m["new_conf"] = conf
                m["group_d"] = float(d[best])
                m["centre"] = centres[-1]
        # a run that is near no swatch and is alone (or far from its group's centre) matches nothing: noise
        for g, members in groups.items():
            for s in members:
                own_ref = float(ref_dists(s["hist"], s["lx"]).min())
                if own_ref > noise_thr and (len(members) == 1 or dist(s, s["centre"]) > group_thr):
                    s["noise"] = True
        # B/W: a group made only of short pieces that match their swatch weakly holds boundary mixes (a thin bed with
        # the neighbouring pattern bleeding in, a broken row at a contact): each piece joins its more similar neighbour
        if not colour:
            for members in groups.values():
                if (members[0]["group_d"] > cfg.lith2_weak_dist
                        and max(m["bottom"] - m["top"] for m in members) < 3 * min_len):
                    for m in members:
                        m["noise"] = True
                        info["mixed"] = info.get("mixed", 0) + 1
    info["noise"] = sum(s["noise"] for s in segs)

    # 4. noise -> the more similar named neighbour (previous or next); none -> blank
    def named(i):
        return 0 <= i < len(segs) and not segs[i]["blank"] and not segs[i]["noise"]

    for i, s in enumerate(segs):
        if not s["noise"]:
            continue
        cands = [segs[j] for j in (i - 1, i + 1) if named(j)]
        if not cands:                                                       # look past other noise runs
            j = i - 1
            while j >= 0 and segs[j]["noise"]:
                j -= 1
            k = i + 1
            while k < len(segs) and segs[k]["noise"]:
                k += 1
            cands = [segs[x] for x in (j, k) if named(x)]
        if cands:
            nb = min(cands, key=lambda c: dist(s, c["centre"]))
            s["new"], s["new_conf"] = nb["new"], 0.0
        else:
            s["new"], s["new_conf"] = LX.BLANK, 0.0
    for s in segs:
        if s["blank"]:
            s["new"], s["new_conf"] = LX.BLANK, 1.0
    info["renamed"] = sum(s["new"] != s["old"] for s in segs)

    # 5. merge equal neighbours (gaps between runs are closed by the boundaries lithology/ gave)
    out = []
    for s in segs:
        if out and out[-1][0] == s["new"]:
            n, a, b, c = out[-1]
            out[-1] = (n, a, s["bottom"], (c * (b - a) + s["new_conf"] * (s["bottom"] - s["top"])) / (s["bottom"] - a))
        else:
            out.append((s["new"], s["top"], s["bottom"], float(s["new_conf"])))
    info["n_output"] = len(out)
    return out, info
