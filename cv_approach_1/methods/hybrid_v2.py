"""
hybrid_v2.py: method "hybrid_v2" of lithology_pipeline.py. Revision of hybrid_pipeline.py after reviewing its logic on
the colour mud log "Callaway 3-33" (thin colour bands, 30-entry legend):

Bugs fixed
    1. Light-coloured patterns were dropped as blank paper. Chalk is drawn with pale cyan lines on a cyan fill: its
       ink density is ~0, and "density < blank_density" made it paper. Blank now means: no ink *and* paper colour
       (bright, no chroma). A coloured fill is never blank.
    2. Colour logs with thin bands were mixed by the texture windows (35 px windows over 10-25 px bands) and squeezed
       into k <= 8 clusters, so three different rocks all became "Limestone". Colour logs are now read row by row
       (colour mode, below); black-and-white logs keep the cluster path (it is what gives 99.5% on the example).
    3. Legend names were glued or junk on mud logs ("Sandstone Shale", "Siltstone Silty Shale", "Tt",
       "Cccccccement", "Metamorphic xxxxxnosample"): one cleaner for every legend source now collapses repeated
       letters, keeps lithology words only ("shaly" included) and drops "Unknown" / "No sample" entries.
    4. The first legend source that "did not look wrong" won even when its names were poor. Every source is now scored
       by name quality; the fast Tesseract finders run first and RapidOCR (slow) only when they are poor.

Colour mode (colour scan and >= 3 coloured legend swatches)
    a. Legend swatches are grouped by fill colour (CIE76 dE < 10): e.g. Sandstone / Salt-Pepper Sand / Clay Choke Sand.
    b. For every row of the column: fill colour (mean Lab of the non-ink pixels) and ink share over a small window
       (half a pattern period), via cumulative sums.
    c. Each row gets the nearest colour group (or blank, or "too dark" -> the darkest swatch, e.g. Coal); rows that
       are mostly ink (separator lines) or far from every group take their neighbours' label.
    d. Mode filter, run-length encoding, runs thinner than a quarter period merged into a neighbour.
    e. Inside a group with several swatches, each band is named by texture: its stroke directions / density against
       resized x0.35-1.5 + thickened 0-3 px copies of each swatch of the group; near-ties are flagged ambiguous.
"""
from __future__ import annotations

import json
import re
import time
import tracemalloc
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
import pandas as pd

from . import cv_pipeline as cv
from . import hybrid_pipeline as hy
from lithology import drawing as LD
from lithology import extract as LX

ProgressFn = Callable[[dict], None]

VOCAB = sorted(set(cv.LITHOLOGY_WORDS + cv.EXTRA_LITHOLOGY_WORDS + ["shaly", "silty", "sandy", "chalky", "cherty"]))
CONNECTORS = {"and", "or", "with"}


# ----------------------------------------------------------------------------------------------------------------------
# Legend names
# ----------------------------------------------------------------------------------------------------------------------
def tidy_name(raw: str) -> str:
    """OCR text -> clean lithology name, or '' when it is junk.
    'Cccccccement' -> 'Cement', 'SHALY SANDSTONE' -> 'Shaly Sandstone', 'Tt' -> '', 'xxxxxNO SAMPLE' -> ''."""
    if re.search(r"unknown|sample", raw or "", re.I):
        return ""
    out = []
    for w in re.findall(r"[A-Za-z]+", raw or ""):
        w = re.sub(r"(.)\1{2,}", r"\1", w.lower())                 # 'cccccccement' -> 'cement'
        if len(w) < 3 and w not in CONNECTORS:
            continue
        if w in CONNECTORS:
            out.append(w)
            continue
        import difflib
        m = difflib.get_close_matches(w, VOCAB, n=1, cutoff=0.8)
        if m:
            out.append(m[0])
        else:                                                      # 'hrebrrrebentonite': a vocabulary word inside
            inner = [v for v in VOCAB if len(v) >= 5 and v in w]
            if inner:
                out.append(max(inner, key=len))
    while out and out[0] in CONNECTORS:
        out.pop(0)
    while out and out[-1] in CONNECTORS:
        out.pop()
    out = [w for i, w in enumerate(out) if i == 0 or w != out[i - 1]]
    return " ".join(w if w in CONNECTORS else w.capitalize() for w in out).replace(" or ", " or ")


def name_quality(swatches) -> float:
    """Higher = better legend reading: many swatches with short, clean names; glued names (more than one rock noun,
    e.g. 'Siltstone Silty Shale') and junk cost points."""
    nouns = {"limestone", "dolomite", "anhydrite", "halite", "salt", "coal", "clay", "claystone", "shale", "silt",
             "siltstone", "sand", "sandstone", "chert", "marl", "marlstone", "conglomerate", "gypsum", "tuff", "granite",
             "chalk", "mudstone", "breccia", "lignite", "gravel", "cement", "bentonite", "till", "siderite", "limonite"}
    score = 0.0
    for s in swatches:
        words = s.name.lower().split()
        n_nouns = sum(w in nouns for w in words)
        if not words:
            score -= 1
        elif n_nouns >= 2 and not ({"and", "or"} & set(words)):
            score -= 0.5                                            # glued: two rocks without a connector
        elif len(words) > 4:
            score -= 1
        else:
            score += 1
    return score


def _retidy(swatches) -> list:
    out, seen = [], set()
    for s in swatches:
        n = tidy_name(s.name)
        if n and n not in seen:
            seen.add(n)
            s.name = n
            out.append(s)
    return out


def normalise_strip_swatches(swatches, leg_ink, leg_rgb) -> list:
    """Strip legends (mud logs: wide, flat swatches in columns, name on the right) share one swatch size and every
    swatch starts at its column's left edge. Fragments found by the text-anchored finder (one 'TT' of Marlstone, one
    'C' of Cement, the letter 'S' of a name) are widened to the column's swatch; boxes spanning two rows are cut to
    one row. A swatch with nothing to its left (Coal's small square) is kept as it is."""
    wide = [s for s in swatches if s.box[2] >= 3 * s.box[3]]
    if len(wide) < 3:
        return swatches                                             # not a strip legend (e.g. square swatches)
    med_w = int(np.median([s.box[2] for s in wide]))
    med_h = int(np.median([s.box[3] for s in wide]))
    xs = sorted(s.box[0] for s in wide)
    cols = [x for i, x in enumerate(xs) if i == 0 or x - xs[i - 1] > 6]
    ys = sorted(s.box[1] for s in wide)
    rows = [y for i, y in enumerate(ys) if i == 0 or y - ys[i - 1] > 3]   # the legend's row grid
    H, W = leg_ink.shape
    if leg_rgb is not None:
        hsv = cv2.cvtColor(leg_rgb, cv2.COLOR_RGB2HSV)
        content = ((leg_rgb.min(2) < 200) | (hsv[..., 1] > 60)).astype(np.uint8) | leg_ink
    else:
        content = leg_ink
    for s in swatches:
        x, y, w, h = s.box
        changed = False
        if h > 1.4 * med_h or h < 0.7 * med_h:                      # two rows, or a fragment: snap to the row grid
            centre = y + h / 2
            y = min(rows, key=lambda r: abs(r + med_h / 2 - centre)) if rows else int(round(centre - med_h / 2))
            h, changed = med_h, True
        if w < 0.6 * med_w:
            c = max((cx for cx in cols if cx <= x + 2), default=None)
            if c is not None and x - c <= med_w + 10 and x > c and content[y:y + h, c:x].mean() > 0.03:
                x, w, changed = c, med_w, True
            elif c is not None and x - c > med_w + 10:
                pass
        if changed:
            x, y = max(0, x), max(0, y)
            w, h = min(w, W - x), min(h, H - y)
            m = 1
            s.box = (int(x), int(y), int(w), int(h))
            s.ink = leg_ink[y + m: y + h - m, x + m: x + w - m]
            s.rgb = leg_rgb[y + m: y + h - m, x + m: x + w - m] if leg_rgb is not None else None
    return swatches


def read_legend_v2(image, boxes, cfg, ccfg) -> tuple:
    leg_box = boxes["lithology_legend"]
    leg_crop = image.crop(tuple(leg_box))
    leg_ink = cv.to_ink(leg_crop, ccfg)
    leg_rgb = np.array(leg_crop.convert("RGB")) if cv.is_colour(leg_crop) else None
    sources = ["rapidocr", "size-groups", "text-anchored"] if cfg.hybrid_prefer_rapidocr else \
        ["size-groups", "text-anchored", "rapidocr"]
    results, tried = [], []
    for src in sources:
        try:
            if src == "size-groups":
                sw = cv.find_swatches(leg_ink, leg_rgb)
            elif src == "text-anchored":
                sw = cv.find_swatches_by_text(leg_ink, leg_rgb)
            else:
                sw = hy.swatches_from_rapidocr(image, leg_box, leg_ink, leg_rgb)
        except Exception as e:                                      # noqa: BLE001
            tried.append(f"{src}: {type(e).__name__}")
            continue
        sw = normalise_strip_swatches(_retidy(sw), leg_ink, leg_rgb)
        q = name_quality(sw)
        tried.append(f"{src}: {len(sw)} (quality {q:.0f})")
        results.append((q, src, sw))
        if len(sw) >= 3 and q >= 0.8 * len(sw):                     # good enough: skip the slower sources
            break
    if not results:
        return [], "none", tried
    q, src, sw = max(results, key=lambda r: r[0])
    return sw, src, tried


# ----------------------------------------------------------------------------------------------------------------------
# Colour helpers (OpenCV 8-bit Lab: L 0-255, a/b offset 128)
# ----------------------------------------------------------------------------------------------------------------------
def delta_e(a, b) -> np.ndarray:
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = a - b
    return np.sqrt((d[..., 0] / 2.55) ** 2 + d[..., 1] ** 2 + d[..., 2] ** 2)


def chroma(lab) -> np.ndarray:
    lab = np.asarray(lab, float)
    return np.hypot(lab[..., 1] - 128, lab[..., 2] - 128)


def is_paper(lab) -> np.ndarray:
    lab = np.asarray(lab, float)
    return (lab[..., 0] > 230) & (chroma(lab) < 7)


def colour_groups(swatches, max_de: float = 10.0) -> list:
    """Indices of swatches grouped by fill colour (union of pairs closer than max_de)."""
    cols = [cv.fill_colour(s.rgb, s.ink) if s.rgb is not None else None for s in swatches]
    parent = list(range(len(swatches)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            if cols[i] is not None and cols[j] is not None and delta_e(cols[i], cols[j]) < max_de:
                parent[root(i)] = root(j)
    groups = {}
    for i in range(len(cols)):
        if cols[i] is not None:
            groups.setdefault(root(i), []).append(i)
    return [dict(members=m, colour=np.mean([cols[i] for i in m], 0)) for m in groups.values()], cols


def use_colour_mode(rgb, swatches) -> bool:
    if rgb is None:
        return False
    cols = [cv.fill_colour(s.rgb, s.ink) for s in swatches if s.rgb is not None]
    return sum(1 for c in cols if chroma(c) > 10) >= 3


# ----------------------------------------------------------------------------------------------------------------------
# Colour mode: row-by-row classification
# ----------------------------------------------------------------------------------------------------------------------
BLANK, UNSURE = -1, -2


def relative_ink(rgb: np.ndarray, contrast: float = 30.0) -> np.ndarray:
    """Pattern strokes of a coloured region: pixels clearly darker than its fill (median lightness), so thin brown
    lines on orange count as strokes even though they are not 'dark ink' (the legend draws them thinner/lighter than
    the column)."""
    L = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)[..., 0].astype(np.float32)
    return (L < np.median(L) - contrast).astype(np.uint8)


def _mode_filter(lab: np.ndarray, half: int) -> np.ndarray:
    """Majority label over a (2*half+1)-row window (labels may be negative)."""
    vals = np.unique(lab)
    H = len(lab)
    best, best_n = lab.copy(), np.zeros(H, int)
    for v in vals:
        c = np.concatenate([[0], np.cumsum(lab == v)])
        lo, hi = np.clip(np.arange(H) - half, 0, H), np.clip(np.arange(H) + half + 1, 0, H)
        n = c[hi] - c[lo]
        better = n > best_n
        best[better], best_n[better] = v, n[better]
    return best


def classify_colour_rows(ink, rgb, swatches, scale, describe, cfg, ccfg) -> tuple:
    """Returns (beds [(name, top, bottom, ambiguous, alternatives)], details)."""
    H, W = ink.shape
    groups, cols = colour_groups(swatches)
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float64)
    paper = (ink == 0)
    w = max(3, int(round(0.5 * scale.period))) | 1                   # rows per colour window (odd)
    cs_lab = np.vstack([np.zeros((1, 3)), np.cumsum((lab * paper[..., None]).sum(1), 0)])
    cs_n = np.concatenate([[0], np.cumsum(paper.sum(1))])
    cs_ink = np.concatenate([[0], np.cumsum(ink.sum(1))])
    r = np.arange(H)
    lo, hi = np.clip(r - w // 2, 0, H), np.clip(r + w // 2 + 1, 0, H)
    n_paper = cs_n[hi] - cs_n[lo]
    fill = (cs_lab[hi] - cs_lab[lo]) / np.maximum(n_paper, 1)[:, None]
    ink_share = (cs_ink[hi] - cs_ink[lo]) / ((hi - lo) * W)

    G = np.stack([g["colour"] for g in groups]) if groups else np.zeros((0, 3))
    D = np.stack([delta_e(fill, g) for g in G], 1) if len(G) else np.full((H, 0), np.inf)
    lab_row = np.full(H, UNSURE)
    if len(G):
        near = D.argmin(1)
        dmin = D[np.arange(H), near]
        lab_row = np.where(dmin < 25, near, UNSURE)
    white_rows = is_paper(fill)
    # paper colour + (almost) no ink = blank, unless the nearest group is itself paper-coloured and there is ink
    lab_row[white_rows & (ink_share < 0.01)] = BLANK
    lab_row[(n_paper < 0.25 * (hi - lo) * W)] = UNSURE                # mostly ink: separator lines, text
    dark = [i for i, s in enumerate(swatches) if s.ink.mean() > 0.7]   # solid black swatch (Coal)
    if dark:
        gi = next((k for k, g in enumerate(groups) if dark[0] in g["members"]), None)
        if gi is not None:
            lab_row[ink_share > 0.85] = gi
    # unsure rows take the label of the nearest sure row
    sure = np.where(lab_row != UNSURE)[0]
    if not len(sure):
        return [], dict(groups=len(groups), rows_unsure=H)
    idx = np.searchsorted(sure, r).clip(0, len(sure) - 1)
    prev = sure[np.maximum(idx - 1, 0)]
    nxt = sure[idx]
    pick = np.where(np.abs(r - prev) <= np.abs(nxt - r), prev, nxt)
    filled = lab_row[pick]
    smooth = _mode_filter(filled, max(1, w // 2))

    # runs; thin runs merged into the longer neighbour
    runs = [list(x) for x in cv.runs(smooth)]
    min_px = max(3, int(round(0.25 * scale.period)))
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for i, (g, a, b) in enumerate(runs):
            if b - a >= min_px:
                continue
            j = 1 if i == 0 else i - 1 if i == len(runs) - 1 else (
                i - 1 if runs[i - 1][2] - runs[i - 1][1] >= runs[i + 1][2] - runs[i + 1][1] else i + 1)
            lo_, hi_ = min(i, j), max(i, j)
            runs[lo_] = [runs[j][0], runs[lo_][1], runs[hi_][2]]
            del runs[hi_]
            k = 0
            while k < len(runs) - 1:
                if runs[k][0] == runs[k + 1][0]:
                    runs[k][2] = runs[k + 1][2]
                    del runs[k + 1]
                else:
                    k += 1
            changed = True
            break

    # names: single-member groups directly; several members -> texture of the band vs that group's swatch variants
    cache = {}
    names_idx = [cv.TEXTURE_FEATURES.index(k) for k in cv.NAMING_FEATURES]
    # tolerances for naming inside a colour group: ink density and blob aspect change with the stroke weight the column
    # is drawn at, so they get a realistic tolerance; stroke directions and blob shape decide
    floor = np.array([dict(cv.FEATURE_FLOOR, density=0.08, log_aspect=0.5)[k] for k in cv.NAMING_FEATURES])

    def variants(i):
        if i not in cache:
            s = swatches[i]
            pat = relative_ink(s.rgb) if s.rgb is not None and s.rgb.size else s.ink
            cache[i] = cv.swatch_variants(cv.Swatch(s.name, s.box, pat, None), W, scale, describe, ccfg)[:, names_idx]
        return cache[i]
    beds = []
    for g, a, b in runs:
        if g == BLANK:
            continue
        members = groups[g]["members"]
        if len(members) == 1:
            beds.append((swatches[members[0]].name, a, b, False, []))
            continue
        f = describe(relative_ink(rgb[a:b]))[names_idx]
        d = np.array([np.min(np.linalg.norm((variants(i) - f) / floor, axis=1)) for i in members])
        order = np.argsort(d)
        best = members[order[0]]
        near = [members[k] for k in order[1:] if d[k] <= (1 + ccfg.tie_ratio) * d[order[0]]]
        if (b - a) < scale.window:                                  # too thin for texture to be reliable
            near = [members[k] for k in order[1:]]
        beds.append((swatches[best].name, a, b, bool(near), sorted({swatches[i].name for i in near})))
    return beds, dict(groups=len(groups), window_rows=w, min_bed_px=min_px,
                      rows_unsure=int((lab_row == UNSURE).sum()), rows_blank=int((smooth == BLANK).sum()))


# ----------------------------------------------------------------------------------------------------------------------
# One log
# ----------------------------------------------------------------------------------------------------------------------
def process_log_v2(log, out_dir: Path, cfg, ccfg, colours, on_progress: ProgressFn, depth_reader=None,
                   method_name: str = "hybrid_v2", parallel_depth: bool = False, legend_plot=None,
                   legend_reader=None) -> dict:
    """depth_reader / method_name / parallel_depth / legend_plot / legend_reader let hybrid_v3 and lithology_cv reuse
    this (Pau's robust depth fit, depth OCR in a background thread while the column is analysed, fast legend plot,
    Pau's grid legend parser)."""
    depth_reader = depth_reader or hy.read_depth
    legend_reader = legend_reader or read_legend_v2
    legend_plot = legend_plot or cv.plot_legend
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
    depth_future = None
    if parallel_depth and "depth_column" in boxes:
        # OCR runs in external processes (Tesseract / onnxruntime release the GIL): read the depth labels on the
        # second core while this thread analyses the column. The page is decoded first: PIL's lazy loading is not
        # thread-safe.
        from concurrent.futures import ThreadPoolExecutor
        image.load()
        pool = ThreadPoolExecutor(max_workers=1)
        depth_future = pool.submit(depth_reader, image, boxes["depth_column"], cfg, ccfg)
        pool.shutdown(wait=False)

    ink, rgb, col_x0, col_y0 = cv.prepare_column(image, boxes["interpreted_lithology_column"], ccfg)
    scale = cv.measure_period(ink, ccfg)
    ink = cv.remove_grid_lines(ink, scale, ccfg)
    describe = cv.TextureDescriber(scale)
    lap("column")

    # legend first: it decides between colour mode and the cluster path
    legend_result = legend_reader(image, boxes, cfg, ccfg)
    if len(legend_result) == 4:
        swatches, legend_method, legend_tried, legend_reader_timings = legend_result
    else:
        swatches, legend_method, legend_tried = legend_result
        legend_reader_timings = {}
    groups = cv.lookalike_groups(swatches, describe)
    colour_mode = cfg.hybrid_colour_mode and use_colour_mode(rgb, swatches)
    lap("legend+naming")

    clusters, silhouette, transitions, k, details = {}, None, [], 0, {}
    raw = []                                                        # (name, top, bottom, ambiguous, cluster key)
    if colour_mode:
        beds, details = classify_colour_rows(ink, rgb, swatches, scale, describe, cfg, ccfg)
        keys = {}
        for name, a, b, amb, alts in beds:
            cid = keys.setdefault(name, len(keys))
            raw.append((name, a, b, amb, cid))
            c = clusters.setdefault(str(cid), dict(name=name, ambiguous=False, alternatives=[], margin=None, distances={}))
            c["ambiguous"] = c["ambiguous"] or amb
            c["alternatives"] = sorted(set(c["alternatives"]) | set(alts))
        k = len(keys)
    else:
        F, centres, dens = cv.window_features(ink, rgb, scale, describe, ccfg)
        labels, X, centroids, sil = cv.cluster_windows(F, ccfg)
        labels, transitions = cv.transition_guard(labels, X, centroids, scale)
        beds = cv.refine_boundaries(ink, cv.labels_to_beds(labels, centres, ink.shape[0], scale), scale, describe)
        # blank = no ink AND paper colour (bug 1: a pale coloured pattern such as Chalk is not paper)
        rock = set()
        for c in np.unique(labels):
            m = np.median(F[labels == c], 0)
            paper_colour = rgb is None or bool(is_paper(m[-3:]))
            if m[0] >= ccfg.blank_density or not paper_colour:
                rock.add(int(c))
        matches = cv.name_clusters({c: np.median(F[labels == c], 0) for c in rock}, swatches, F, ink.shape[1], scale,
                                   describe, groups, ccfg)
        clusters = {str(c): asdict(m) for c, m in matches.items()}
        for c, a, b in beds:
            if c in matches:
                raw.append((matches[c].name, a, b, matches[c].ambiguous, int(c)))
        silhouette, k = round(sil, 3), int(len(np.unique(labels)))
    lap("segmentation")

    y_to_depth, depth_info, depth_how = (None, None, ["no depth_column box"])
    if depth_future is not None:
        y_to_depth, depth_info, depth_how = depth_future.result()      # usually finished already
    elif "depth_column" in boxes:
        y_to_depth, depth_info, depth_how = depth_reader(image, boxes["depth_column"], cfg, ccfg)
    lap("depth")

    def depth(y):
        if y_to_depth is None:
            return None
        v = float(y_to_depth(y))
        return round(float(np.round(v / cfg.round_depth) * cfg.round_depth), 4) if cfg.round_depth else round(v, 2)

    intervals = []
    for name, a, b, amb, cid in raw:
        ty, by = int(a + col_y0), int(b + col_y0)
        p = intervals[-1] if intervals else None
        if cfg.merge_same_name and p and p["lithology"] == name != "Unknown" and p["bottom_y"] == ty:
            p.update(bottom_y=by, bottom_depth=depth(by), ambiguous=p["ambiguous"] or amb)
        else:
            intervals.append(dict(top_y=ty, bottom_y=by, cluster_id=cid, lithology=name, top_depth=depth(ty),
                                  bottom_depth=depth(by), ambiguous=bool(amb)))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

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
    cv.render(image, {kk: v for kk, v in boxes.items() if kk != "lithology_legend"}, intervals, colours, region,
              cfg.column_scale).save(column_png)
    legend_png = legend_plot(swatches, groups, out_dir / f"{log.stem}_legend.png")
    fit = None if depth_info is None else cv.DepthFit(depth_info["a"], depth_info["b"], depth_info["n_used"],
                                                      depth_info["n_read"], depth_info["points"])
    depth_png = cv.plot_depth(fit, out_dir / f"{log.stem}_depth.png")
    tif_path, tif_error, tif_layout = None, None, None
    if cfg.annotated_tif and intervals and depth_info and "depth_column" in boxes:
        try:
            tif_out = out_dir / f"{log.stem}_annotated.tif"
            tif_layout = LD.render_tif(image, log.annotation_path, dict(intervals=intervals), tif_out)  # returns its layout
            tif_path = str(tif_out)
        except Exception as e:                                      # noqa: BLE001
            tif_error = f"{type(e).__name__}: {e}"
    lap("render")

    record = dict(
        stem=log.stem, image=str(log.image_path), method=method_name, colour_scan=rgb is not None,
        segmentation="colour rows" if colour_mode else "texture clusters", segmentation_details=details,
        scale=asdict(scale), k=k, silhouette=silhouette, transition_clusters_removed=transitions,
        n_intervals=len(intervals), legend_method=f"{legend_method} (tried {', '.join(legend_tried)})",
        legend_reader_timings_s=legend_reader_timings,
        swatches=[dict(name=s.name, look_alikes=[swatches[j].name for j in groups[i] if j != i]) for i, s in enumerate(swatches)],
        clusters=clusters, ambiguous=sorted({r["lithology"] for r in intervals if r["ambiguous"]}),
        depth=None if depth_info is None else {kk: v for kk, v in depth_info.items() if kk != "points"},
        depth_error=None if depth_info else "no depth: " + "; ".join(depth_how), depth_tried=depth_how,
        intervals=intervals, timings_s=timings, runtime_s=round(sum(timings.values()) - timings["render"], 3),
        peak_mb=round(peak / 1e6, 1),
        files=dict(csv=str(csv_path), page_png=str(page), column_png=str(column_png), legend_png=legend_png,
                   depth_png=depth_png, annotated_tif=tif_path),
        annotated_tif_error=tif_error, annotated_tif_layout=tif_layout,
    )
    if log.labels_path:
        record["metrics"] = cv.evaluate(intervals, pd.read_csv(log.labels_path).to_dict("records"))
    return record
