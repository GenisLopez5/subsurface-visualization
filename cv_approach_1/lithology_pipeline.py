#!/usr/bin/env python3
"""
lithology_pipeline.py: extract the interpreted lithology column of scanned well logs as structured data.

For each log (image + annotation JSON with bounding boxes) the pipeline produces:
    * <stem>.csv            one row per interval: top_depth, bottom_depth, lithology, top_y, bottom_y, cluster_id
    * <stem>_page.png       the whole page painted in the organisers' style (boxes, intervals, depths, clusters)
    * <stem>_column.png     zoomed view of the depth + lithology columns with the prediction painted on
    * <stem>_legend.png     the legend swatches that were found, the names read by OCR, look-alike warnings
    * <stem>_depth.png      the depth labels read by OCR and the fitted pixel -> depth line
and one summary.json for the whole folder (plus metrics when ground-truth labels are available).

How it works (classic computer vision, CPU only, ~1-2 s per log)
------------------------------------------------------------------
 1. Binarise      1-bit, grey or colour scans -> ink mask (1 = ink); scanner specks removed (area opening).
 2. Column        crop the lithology column, strip its frame (projection profiles), keep the widest gap between
                  track borders, and blank isolated full-width grid lines (depth ticks), keeping pattern lines.
 3. Scale         measure the vertical pattern period (autocorrelation) and size every window/kernel from it,
                  so logs scanned at different resolutions behave the same.
 4. Features      a window slides down the column; each window is described by ink density, the share of ink in
                  horizontal / vertical / diagonal strokes (directional morphological openings), blob shape and
                  count (connected components) and, for colour scans, the fill colour (CIELAB).
 5. Segmentation  k-means on the standardised features (k by silhouette score), a guard that removes "transition"
                  clusters made of windows straddling two rocks, run-length encoding into intervals, and a
                  row-level refinement that snaps every boundary to the exact pixel row where the texture changes.
 6. Legend        swatches found by dilation + connected components (same-size group with the largest area),
                  names read with Tesseract OCR and snapped to a lithology vocabulary.
 7. Naming        every cluster is matched to the nearest legend swatch using scale-invariant features only (stroke
                  directions, blob aspect, dot-like share, continuous-line share, density, fill colour). The legend is drawn at a different
                  scale and stroke weight than the column, so each swatch is also resized and thickened into many
                  variants before comparing. Look-alike swatches (e.g. Shale vs Clay drawn identically) are detected and
                  resolved by a commonness prior; those matches and near-ties (within 10%) are flagged as ambiguous.
 8. Depth         the depth labels are OCR'd from the rotated depth column; a RANSAC line maps pixels to depth.

Usage
-----
    ./setup_venv.sh && source .venv/bin/activate         # once (installs everything inside .venv, no sudo)
    python lithology_pipeline.py data/ --out outputs/     # every <stem>.json + image in data/
    python lithology_pipeline.py data/ --out outputs/ --labels data/labels   # + accuracy vs ground truth

    from lithology_pipeline import run_folder, process_log, Config     # as a library
    summary = run_folder("data", "outputs", on_progress=print)

Ground truth (optional): labels/<stem>.csv with the organisers' example.csv columns. The organisers' example.csv
is adopted automatically for the example log.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import shutil
import sys
import time
import tracemalloc
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

import matplotlib
matplotlib.use("Agg")                                        # render to files, no display needed
import matplotlib.pyplot as plt
import cv2
import numpy as np
import pandas as pd
import pytesseract
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import median_filter
from scipy.signal import find_peaks
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

Image.MAX_IMAGE_PIXELS = None                                 # well-log scans are huge (e.g. 4864 x 12050 px)

# Use the Tesseract copy installed inside the virtualenv by setup_venv.sh, even if the venv is not activated.
_VENV_TESSERACT = Path(sys.executable).parent / "tesseract"
if _VENV_TESSERACT.exists():
    pytesseract.pytesseract.tesseract_cmd = str(_VENV_TESSERACT)

ProgressFn = Callable[[dict], None]


# ======================================================================================================================
# Configuration
# ======================================================================================================================
@dataclass
class Config:
    """All tunable parameters. Lengths marked 'periods' are multiples of the measured pattern period P (px), so the
    defaults reproduce the values tuned on the example log (P = 25 px) and scale with each log's resolution."""
    # --- scale -----------------------------------------------------------------------------------------------------
    auto_scale: bool = True             # measure P per log; if False, use default_period
    default_period: float = 25.0        # px, used when P cannot be measured
    period_range: tuple = (8.0, 80.0)   # plausible P (px); measurements outside are ignored
    # --- windows & segmentation (in periods) -------------------------------------------------------------------------
    window_periods: float = 2.9         # window height (72 px at P=25): more repeats = steadier features
    stride_fraction: float = 1 / 9      # window step as a fraction of the window height (8 px at P=25)
    min_bed_periods: float = 2.4        # beds thinner than this are merged into a neighbour (60 px at P=25)
    smooth_windows: int = 7             # median filter length along depth (in windows), removes "phase" jitter
    k_range: tuple = (2, 8)             # number of clusters tried; chosen by silhouette score
    single_rock_silhouette: float = 0.2  # below this, the column is treated as one rock (k = 1)
    refine_radius_periods: float = 1.9  # boundary refinement search radius (48 px at P=25)
    refine_window_periods: float = 1.6  # rows compared above/below a candidate cut (40 px at P=25)
    # --- morphology (in periods) -------------------------------------------------------------------------------------
    stroke_periods: float = 0.36        # directional opening length (9 px at P=25)
    diag_periods: float = 0.28          # diagonal opening length (7 px at P=25)
    frame_line_px: int = 61             # vertical lines at least this long near the column edges = frame
    border_cover: float = 0.5           # a vertical line covering > 50% of the column height is a track border
    grid_isolation_periods: float = 2.0  # full-width horizontal lines with no neighbour within this are grid lines
    speck_area: int = 4                 # ink/paper components of <= this many px are scanner noise
    blank_density: float = 0.005        # windows with less ink are blank paper
    # --- legend & naming --------------------------------------------------------------------------------------------
    swatch_scales: tuple = (0.35, 0.5, 0.65, 0.8, 1.0, 1.25, 1.5)
    swatch_dilations: tuple = (0, 1, 2, 3)
    tie_ratio: float = 0.10             # swatches within 10% of the best distance are a tie
    text_legend_fallback: bool = True   # if few/garbled swatches: find swatches next to the OCR'd names instead
    merge_same_name: bool = True        # merge neighbouring intervals that got the same lithology name
    # --- depth ---------------------------------------------------------------------------------------------------------
    depth_inlier_px: float = 15.0       # RANSAC inlier tolerance (px)
    depth_max_strips: int = 4           # OCR at most this many evenly spaced 8000-row strips of the depth column
    depth_upright_fallback: bool = True  # if sideways labels give < 3 inliers, also read upright labels ("3,000")
    # --- output --------------------------------------------------------------------------------------------------------
    page_scale: float = 0.25            # size of <stem>_page.png relative to the scan
    column_scale: float = 0.5           # size of <stem>_column.png relative to the scan
    column_full_width: bool = True      # <stem>_column.png shows the whole page width (else only the two columns)


@dataclass
class Scale:
    """Pixel sizes derived from the pattern period of one log."""
    period: float
    measured: bool
    window: int
    stride: int
    min_bed: int
    refine_radius: int
    refine_window: int
    stroke: int
    diag: int

    @classmethod
    def from_period(cls, p: float, measured: bool, cfg: Config) -> "Scale":
        window = max(24, int(round(cfg.window_periods * p)))
        return cls(period=float(p), measured=measured, window=window,
                   stride=max(2, int(round(window * cfg.stride_fraction))),
                   min_bed=max(16, int(round(cfg.min_bed_periods * p))),
                   refine_radius=max(8, int(round(cfg.refine_radius_periods * p))),
                   refine_window=max(8, int(round(cfg.refine_window_periods * p))),
                   stroke=max(3, int(round(cfg.stroke_periods * p))) | 1,      # odd kernel lengths
                   diag=max(3, int(round(cfg.diag_periods * p))) | 1)


# ======================================================================================================================
# Data model
# ======================================================================================================================
@dataclass
class Log:
    """One input document."""
    stem: str
    image_path: Path
    annotation_path: Path
    labels_path: Optional[Path] = None


@dataclass
class Swatch:
    name: str
    box: tuple                                   # x, y, w, h inside the legend crop
    ink: np.ndarray = field(repr=False)          # inner pattern, 1 = ink
    rgb: Optional[np.ndarray] = field(default=None, repr=False)


@dataclass
class Interval:
    top_y: int                                   # full-image pixel rows
    bottom_y: int
    cluster_id: int
    lithology: str = "Unknown"
    top_depth: Optional[float] = None
    bottom_depth: Optional[float] = None
    ambiguous: bool = False


@dataclass
class Match:
    name: str
    ambiguous: bool
    alternatives: list
    margin: float                                # (2nd best - best) / best distance
    distances: dict                              # top-3 swatch name -> distance


# ======================================================================================================================
# Stage 1 - Binarisation
# ======================================================================================================================
def remove_specks(mask: np.ndarray, max_area: int) -> np.ndarray:
    """Area opening: delete 8-connected components of <= max_area pixels."""
    _, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    small = np.where(stats[:, cv2.CC_STAT_AREA] <= max_area)[0]
    small = small[small != 0]
    return np.where(np.isin(labels, small), 0, mask).astype(np.uint8)


def is_colour(crop: Image.Image) -> bool:
    """True if the crop has meaningful colour (more than 2% of pixels clearly saturated)."""
    if crop.mode in ("1", "L", "LA", "I", "I;16", "F"):
        return False
    hsv = cv2.cvtColor(np.array(crop.convert("RGB")), cv2.COLOR_RGB2HSV)
    return float((hsv[..., 1] > 50).mean()) > 0.02


def to_ink(crop: Image.Image, cfg: Config) -> np.ndarray:
    """Ink mask (uint8, 1 = ink) of a PIL crop.

    * 1-bit scans: black pixels.
    * grey scans:  Otsu threshold.
    * colour scans: ink is *dark*, while fills can be strongly coloured but are rarely dark. Threshold the value channel
      (max of R,G,B) with Otsu, capped at 110 so dark-ish fills (brown, green) are not mistaken for ink.
    Then remove isolated ink specks and fill isolated paper holes inside strokes (scanner noise).
    """
    if crop.mode == "1":
        ink = (np.array(crop) == 0).astype(np.uint8)
    elif is_colour(crop):
        v = np.array(crop.convert("RGB")).max(axis=2)
        t, _ = cv2.threshold(v, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        ink = (v <= min(t, 110)).astype(np.uint8)              # Otsu's dark class is v <= t (t can be 0)
    else:
        g = np.array(crop.convert("L"))
        _, ink = cv2.threshold(g, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        ink = ink.astype(np.uint8)
    return 1 - remove_specks(1 - remove_specks(ink, cfg.speck_area), cfg.speck_area)


# ======================================================================================================================
# Stage 2 - Column preparation
# ======================================================================================================================
def remove_frame_lines(ink: np.ndarray, min_len: int, edge_fraction: float = 0.08) -> np.ndarray:
    """Remove long *vertical* lines near the left/right edges (frame remnants of a slightly tilted scan).

    Horizontal lines are kept on purpose: limestone/dolomite patterns are made of long horizontal lines, and boundary
    lines drawn by the log author only help the boundary refinement.
    """
    vert = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, min_len)))
    edge = int(np.ceil(edge_fraction * ink.shape[1]))
    vert[:, edge:ink.shape[1] - edge] = 0
    return ink & (1 - cv2.dilate(vert, np.ones((3, 3), np.uint8)))


def prepare_column(image: Image.Image, box, cfg: Config, thresh: float = 0.6, pad: int = 4):
    """Crop the lithology column and strip its frame.

    Projection profiles: frame columns/rows are almost fully inked, so the inside of the frame is where the ink
    fraction drops below `thresh`. Returns (ink, rgb or None, x0, y0) with x0/y0 the full-image offset of the crop.
    """
    crop = image.crop(tuple(box))
    ink = to_ink(crop, cfg)
    inside_x = np.where(ink.mean(0) < thresh)[0]
    x0, x1 = inside_x[0] + pad, inside_x[-1] - pad + 1
    inside_y = np.where(ink[:, x0:x1].mean(1) < thresh)[0]
    y0, y1 = inside_y[0], inside_y[-1] + 1
    bx0, bx1 = track_between_borders(ink[y0:y1, x0:x1], cfg)    # the box may include a sliver of a neighbour track
    x0, x1 = x0 + bx0, x0 + bx1
    rgb = np.array(crop.convert("RGB"))[y0:y1, x0:x1] if is_colour(crop) else None
    return remove_frame_lines(ink[y0:y1, x0:x1], cfg.frame_line_px), rgb, int(box[0] + x0), int(box[1] + y0)


def track_between_borders(ink: np.ndarray, cfg: Config, pad: int = 4) -> tuple:
    """Columns (x0, x1) of the widest gap between track borders inside the crop.

    A track border is a vertical line covering more than `border_cover` of the column height. Annotation boxes are
    sometimes a bit wide and include a border plus a sliver of the neighbouring track; that sliver must not be analysed.
    """
    H, W = ink.shape
    # short probe (200 px) + coverage summed over a 9-px band: long scans are slightly tilted, so a border line drifts
    # sideways by a few pixels over tens of thousands of rows
    vert = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, min(H // 3, 200))))
    cover = np.convolve(vert.mean(0), np.ones(9), mode="same")
    is_border = cover > cfg.border_cover
    edges = [-1] + [x for x in range(W) if is_border[x] and (x == 0 or not is_border[x - 1])] + [W]
    ends = [-1] + [x for x in range(W) if is_border[x] and (x == W - 1 or not is_border[x + 1])] + [W]
    gaps = [(ends[i] + 1, edges[i + 1]) for i in range(len(edges) - 1)]
    a, b = max(gaps, key=lambda g: g[1] - g[0])
    if b - a < 0.3 * W:                                           # implausible: keep the whole crop
        return 0, W
    return (a + pad if a > 0 else 0), (b - pad if b < W else W)


def remove_grid_lines(ink: np.ndarray, scale: "Scale", cfg: Config) -> np.ndarray:
    """Blank isolated full-width horizontal lines (depth grid lines, tick marks, bed boundary lines).

    A line is "isolated" if no other full-width line lies within grid_isolation_periods pattern periods. Limestone /
    dolomite brick patterns are full-width lines too, but they repeat every period, so they are kept.
    """
    H, W = ink.shape
    horiz = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, int(0.8 * W)), 1)))
    rows = np.where(horiz.any(1))[0]
    if not len(rows):
        return ink
    groups = np.split(rows, np.where(np.diff(rows) > 1)[0] + 1)
    centres = np.array([g.mean() for g in groups])
    gap = cfg.grid_isolation_periods * scale.period
    half = max(3, int(round(0.35 * scale.period)))               # also clears the tick marks drawn on the line
    out = ink.copy()
    for i, c in enumerate(centres):
        others = np.delete(centres, i)
        if not len(others) or np.min(np.abs(others - c)) > gap:
            out[max(0, int(c) - half): int(c) + half + 1] = 0
    return out


# ======================================================================================================================
# Stage 3 - Scale (pattern period)
# ======================================================================================================================
def measure_period(ink: np.ndarray, cfg: Config, block: int = 400) -> Scale:
    """Vertical pattern period P: first autocorrelation peak of the row-ink profile, median over 400-row blocks."""
    if cfg.auto_scale:
        prof = ink.sum(1).astype(float)
        lo, hi = cfg.period_range
        found = []
        for s in range(0, max(1, len(prof) - block), block // 2):
            p = prof[s:s + block] - prof[s:s + block].mean()
            if p.std() < 1e-6:
                continue                                       # blank block
            ac = np.correlate(p, p, "full")[len(p) - 1:]
            ac /= ac[0]
            peaks, _ = find_peaks(ac[int(lo):int(hi) + 1], height=0.2)
            if len(peaks):
                found.append(peaks[0] + int(lo))
        if found:
            return Scale.from_period(float(np.median(found)), True, cfg)
    return Scale.from_period(cfg.default_period, False, cfg)


# ======================================================================================================================
# Stage 4 - Features
# ======================================================================================================================
TEXTURE_FEATURES = ["density", "h_frac", "v_frac", "d_frac", "log_aspect", "compact_frac", "hline_frac",
                    "blob_w", "blob_h", "blobs_per_kpx"]
DIRECTION_FEATURES = ["h_frac", "v_frac", "d_frac"]
COLOUR_FEATURES = ["fill_L", "fill_a", "fill_b"]
# Smallest variation of each feature that is meaningful (its "noise floor"). Standardising divides by
# max(std, floor): a feature that is almost constant in a log (e.g. diagonal strokes in a log without anhydrite) would
# otherwise turn its tiny noise blips into huge distances and split one rock into two clusters.
FEATURE_FLOOR = dict(density=0.02, h_frac=0.1, v_frac=0.1, d_frac=0.1, log_aspect=0.2, compact_frac=0.1, hline_frac=0.1,
                     blob_w=1.0, blob_h=1.0, blobs_per_kpx=0.3, fill_L=3.0, fill_a=3.0, fill_b=3.0)
# Features that do not depend on how large the pattern is drawn. Used to compare the column with the legend, which is
# drawn at a different scale and stroke weight. (Clustering inside one column uses all features.)
NAMING_FEATURES = ["density", "h_frac", "v_frac", "d_frac", "log_aspect", "compact_frac", "hline_frac"]


class TextureDescriber:
    """Hand-crafted texture features of a binary patch, with kernels sized from the log's scale."""

    def __init__(self, scale: Scale):
        self.kh = cv2.getStructuringElement(cv2.MORPH_RECT, (scale.stroke, 1))
        self.kv = cv2.getStructuringElement(cv2.MORPH_RECT, (1, scale.stroke))
        self.kd1 = np.eye(scale.diag, dtype=np.uint8)
        self.kd2 = np.fliplr(self.kd1).copy()

    def opened(self, b, k):
        return cv2.morphologyEx(b, cv2.MORPH_OPEN, k)

    def __call__(self, b: np.ndarray) -> np.ndarray:
        """density; share of ink in horizontal / vertical / diagonal strokes; median blob log(width/height); share of
        ink in compact (dot-like, aspect < 2) blobs; share of ink in continuous horizontal lines longer than half the
        patch width (limestone/dolomite bricks, not dashes); median blob width / height; blob count per 1000 px."""
        ink = b.sum()
        if ink < 20:
            return np.zeros(len(TEXTURE_FEATURES))
        _, _, st, _ = cv2.connectedComponentsWithStats(b, connectivity=8)
        st = st[1:]
        st = st[st[:, cv2.CC_STAT_AREA] >= 3]
        w, h = (st[:, 2], st[:, 3]) if len(st) else (np.zeros(1), np.zeros(1))
        area = st[:, cv2.CC_STAT_AREA] if len(st) else np.zeros(1)
        compact = (np.maximum(w, h) < 2 * np.minimum(w, h)) & (np.maximum(w, h) < 3 * max(3, self.kh.shape[1]))
        return np.array([
            b.mean(),
            self.opened(b, self.kh).sum() / ink,
            self.opened(b, self.kv).sum() / ink,
            (self.opened(b, self.kd1) | self.opened(b, self.kd2)).sum() / ink,
            float(np.median(np.log((w + 1) / (h + 1)))),
            float(area[compact].sum() / max(1, area.sum())),
            self.opened(b, cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, b.shape[1] // 2), 1))).sum() / ink,
            float(np.median(w)), float(np.median(h)),
            1000 * len(st) / b.size,
        ])


def fill_colour(rgb: np.ndarray, ink: np.ndarray) -> np.ndarray:
    """Mean CIELAB colour of the paper (non-ink) pixels: the fill colour of a coloured pattern."""
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(float)
    paper = ink.reshape(-1) == 0
    sel = lab[paper] if paper.sum() > 20 else lab
    return sel.mean(0) if len(sel) else np.zeros(3)


def window_features(ink, rgb, scale: Scale, describe: TextureDescriber, cfg: Config):
    """Feature matrix (n_windows x n_features), smoothed along depth, plus window centres and ink densities."""
    starts = np.arange(0, ink.shape[0] - scale.window + 1, scale.stride)
    centres = starts + scale.window // 2
    F = np.array([describe(ink[s:s + scale.window]) for s in starts])
    if rgb is not None:
        C = np.array([fill_colour(rgb[s:s + scale.window], ink[s:s + scale.window]) for s in starts])
        F = np.hstack([F, C])
    dens = F[:, 0].copy()
    return median_filter(F, size=(cfg.smooth_windows, 1), mode="nearest"), centres, dens


# ======================================================================================================================
# Stage 5 - Segmentation
# ======================================================================================================================
def runs(seq) -> list:
    """Run-length encoding: [[label, start, end_exclusive], ...]."""
    out, s = [], 0
    for i in range(1, len(seq) + 1):
        if i == len(seq) or seq[i] != seq[s]:
            out.append([int(seq[s]), s, i])
            s = i
    return out


class FlooredScaler:
    """Standardise each feature by max(std, FEATURE_FLOOR[feature]) (see FEATURE_FLOOR)."""

    def __init__(self, names: list):
        self.floor = np.array([FEATURE_FLOOR[n] for n in names])

    def fit(self, F: np.ndarray) -> "FlooredScaler":
        self.mean, self.scale = F.mean(0), np.maximum(F.std(0), self.floor)
        return self

    def transform(self, F: np.ndarray) -> np.ndarray:
        return (F - self.mean) / self.scale

    def fit_transform(self, F: np.ndarray) -> np.ndarray:
        return self.fit(F).transform(F)


def feature_names(F: np.ndarray) -> list:
    return TEXTURE_FEATURES + (COLOUR_FEATURES if F.shape[1] > len(TEXTURE_FEATURES) else [])


def cluster_windows(F: np.ndarray, cfg: Config):
    """k-means on standardised features (noise-floored); k chosen by silhouette. Returns labels, X, centroids, silhouette."""
    X = FlooredScaler(feature_names(F)).fit_transform(F)
    if len(X) < 4 or np.allclose(X.std(0), 0):
        return np.zeros(len(X), int), X, X.mean(0, keepdims=True), 0.0
    best = None
    for k in range(cfg.k_range[0], min(cfg.k_range[1], len(X) - 1) + 1):
        km = KMeans(k, n_init=10, random_state=0).fit(X)
        # silhouette is O(n^2): score a fixed random sample on long logs (thousands of windows)
        s = silhouette_score(X, km.labels_, sample_size=min(len(X), 3000), random_state=0)
        if best is None or s > best[0]:
            best = (s, km)
    s, km = best
    if s < cfg.single_rock_silhouette:                       # no real structure: one rock
        return np.zeros(len(X), int), X, X.mean(0, keepdims=True), float(s)
    return km.labels_, X, km.cluster_centers_, float(s)


def transition_guard(labels, X, centroids, scale: Scale):
    """Reassign "transition" clusters: windows that straddle a boundary see half of each rock and can form their own
    cluster (shale + sandstone can even look like "silty sandstone"). A transition cluster has only short runs
    (< 2 windows' worth of rows) and those runs sit between two *different* rocks. A thin real bed is usually enclosed
    by the same rock on both sides, so it is kept."""
    rs = runs(labels)
    fake = []
    for c in np.unique(labels):
        mine = [i for i, r in enumerate(rs) if r[0] == c]
        longest = max((rs[i][2] - rs[i][1]) * scale.stride for i in mine)
        between = [0 < i < len(rs) - 1 and rs[i - 1][0] != rs[i + 1][0] for i in mine]
        if longest < 2 * scale.window and np.mean(between) >= 0.8:
            fake.append(int(c))
    real = [int(c) for c in np.unique(labels) if c not in fake]
    if not fake or not real:
        return labels, []
    out = labels.copy()
    for i in np.where(np.isin(labels, fake))[0]:
        out[i] = real[int(np.argmin(np.linalg.norm(centroids[real] - X[i], axis=1)))]
    return out, fake


def labels_to_beds(labels, centres, height, scale: Scale) -> list:
    """Window labels -> beds [(cluster, top_row, bottom_row)] in column coordinates.

    1. mode filter (majority over 5 windows) removes isolated flips;
    2. run-length encoding;
    3. runs thinner than min_bed are merged into the longer neighbour;
    4. a boundary sits halfway between the last window of one run and the first of the next.
    """
    lab = np.array([np.bincount(labels[max(0, i - 2): i + 3]).argmax() for i in range(len(labels))])
    rs = runs(lab)
    changed = True
    while changed and len(rs) > 1:
        changed = False
        for i, (_, a, b) in enumerate(rs):
            if (b - a) * scale.stride >= scale.min_bed:
                continue
            if i == 0:
                j = 1
            elif i == len(rs) - 1:
                j = i - 1
            else:
                j = i - 1 if rs[i - 1][2] - rs[i - 1][1] >= rs[i + 1][2] - rs[i + 1][1] else i + 1
            lo, hi = min(i, j), max(i, j)
            rs[lo] = [rs[j][0], rs[lo][1], rs[hi][2]]
            del rs[hi]
            k = 0
            while k < len(rs) - 1:                                # fuse equal neighbours
                if rs[k][0] == rs[k + 1][0]:
                    rs[k][2] = rs[k + 1][2]
                    del rs[k + 1]
                else:
                    k += 1
            changed = True
            break
    beds = []
    for i, (c, a, b) in enumerate(rs):
        top = 0 if i == 0 else int((centres[a - 1] + centres[a]) / 2)
        bottom = height if i == len(rs) - 1 else int((centres[b - 1] + centres[b]) / 2)
        beds.append([c, top, bottom])
    return beds


def refine_boundaries(ink, beds, scale: Scale, describe: TextureDescriber) -> list:
    """Snap every boundary to the exact row where the texture changes.

    Per pixel row: ink in horizontal strokes, in vertical strokes, and the rest. Around each boundary (± radius) every
    candidate row c is scored by |mean(rows c-w..c) - mean(rows c..c+w)|; cumulative sums make each score O(1).
    """
    h = describe.opened(ink, describe.kh).sum(1).astype(float)
    v = describe.opened(ink, describe.kv).sum(1).astype(float)
    P = np.stack([h, v, ink.sum(1) - h - v], 1)
    cs = np.vstack([np.zeros((1, 3)), np.cumsum(P, 0)])
    w, r = scale.refine_window, scale.refine_radius
    for i in range(1, len(beds)):
        y = beds[i][1]
        lo = max(w, y - r, beds[i - 1][1] + w)
        hi = min(len(P) - w, y + r, beds[i][2] - w)
        if hi <= lo:
            continue
        c = np.arange(lo, hi)
        score = np.abs((cs[c] - cs[c - w]) - (cs[c + w] - cs[c])).sum(1)
        beds[i][1] = beds[i - 1][2] = int(c[np.argmax(score)])
    return beds


# ======================================================================================================================
# Stage 6 - Legend
# ======================================================================================================================
LITHOLOGY_WORDS = ["limestone", "dolomite", "anhydrite", "halite", "salt", "coal", "clay", "claystone", "shale", "silt",
                   "siltstone", "silty", "sand", "sandstone", "sandy", "and", "chert", "marl", "conglomerate", "gypsum",
                   "tuff", "volcanic", "granite", "basement", "chalk", "mudstone", "calcareous", "dolomitic", "shaly",
                   "argillaceous", "igneous", "breccia", "evaporite", "lignite", "gravel", "boulders", "cement"]


# extra words seen in mud-log legends (used only by the text-anchored legend fallback, so the original path is unchanged)
EXTRA_LITHOLOGY_WORDS = ["bentonite", "metamorphic", "till", "siderite", "limonite", "marlstone", "pepper", "colored",
                         "coloured", "gray", "grey", "choke", "welded", "quartzite", "schist", "shell", "pyrite",
                         "glauconite", "oolitic", "fossil", "sandy", "limy", "carbonaceous"]


def clean_label(raw: str, vocab: Optional[list] = None) -> str:
    """OCR text -> lithology name: keep words that fuzzily match the vocabulary ('Slity' -> 'Silty')."""
    vocab = vocab or LITHOLOGY_WORDS
    words = [m[0] for w in re.findall(r"[A-Za-z]{2,}", raw)
             if (m := difflib.get_close_matches(w.lower(), vocab, n=1, cutoff=0.7))]
    return " ".join(words).title()


def read_swatch_name(leg_ink, box) -> Optional[str]:
    """OCR the text under the swatch, or to its right if nothing is found below."""
    x, y, w, h = box
    H, W = leg_ink.shape
    for region in (leg_ink[y + h: min(H, y + h + int(0.9 * h)), max(0, x - w // 4): min(W, x + w + w // 4)],
                   leg_ink[y: y + h, x + w: min(W, x + 3 * w)]):
        if region.size:
            txt = pytesseract.image_to_string(Image.fromarray(((1 - region) * 255).astype(np.uint8)), config="--psm 6")
            name = clean_label(txt)
            if name:
                return name
    return None


def find_swatches(leg_ink, leg_rgb) -> list:
    """Legend swatches with their OCR names.

    Dilation merges each swatch (frame + pattern) into one blob; connected components give candidate boxes. Swatches of
    one legend share a size, so candidates are grouped by size and the group with the largest *total area* wins (big
    title letters can outnumber the swatches, but cover far less area). Empty swatches and swatches without a readable
    name are dropped.
    """
    blob = cv2.dilate(leg_ink, np.ones((9, 9), np.uint8))
    _, _, st, _ = cv2.connectedComponentsWithStats(blob, connectivity=8)
    H, W = leg_ink.shape
    cand = [tuple(map(int, s[:4])) for s in st[1:]
            if 0.05 * W < s[2] < 0.5 * W and 0.04 * H < s[3] < 0.5 * H and 0.8 < s[2] / s[3] < 2.5]
    if not cand:
        return []

    def same_size(a, b):
        return abs(a[2] - b[2]) < 0.15 * b[2] and abs(a[3] - b[3]) < 0.15 * b[3]

    ref = max(cand, key=lambda c: sum(o[2] * o[3] for o in cand if same_size(o, c)))
    swatches = []
    for x, y, w, h in cand:
        if not same_size((x, y, w, h), ref):
            continue
        m = int(0.12 * min(w, h))                                # inner margin skips the frame
        inner = leg_ink[y + m: y + h - m, x + m: x + w - m]
        if inner.mean() <= 0.005:
            continue                                             # empty swatch
        name = read_swatch_name(leg_ink, (x, y, w, h))
        if name:
            rgb = leg_rgb[y + m: y + h - m, x + m: x + w - m] if leg_rgb is not None else None
            swatches.append(Swatch(name=name, box=(x, y, w, h), ink=inner, rgb=rgb))
    return sorted(swatches, key=lambda s: (s.box[1] // 20, s.box[0]))


def swatches_look_wrong(swatches) -> bool:
    """Too few swatches, or names that are really a whole legend row glued together."""
    return len(swatches) < 3 or any(len(s.name.split()) > 4 for s in swatches)


def find_swatches_by_text(leg_ink, leg_rgb) -> list:
    """Fallback legend reader for layouts the size-group finder misses (e.g. mud logs: many wide, flat swatches in
    several columns with the name to the *right*).

    1. OCR the whole legend (upscaled so small print is readable) and group words on one row into phrases.
    2. Keep phrases that contain lithology words (titles, 'Unknown', 'No sample' are dropped).
    3. The swatch of a phrase is the patterned / coloured block immediately to its left, on the same row.
    """
    H, W = leg_ink.shape
    up = 3 if H * W * 9 < 40e6 else 2 if H * W * 4 < 40e6 else 1
    grey = cv2.cvtColor(leg_rgb, cv2.COLOR_RGB2GRAY) if leg_rgb is not None else ((1 - leg_ink) * 255).astype(np.uint8)
    big = cv2.resize(grey, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC) if up > 1 else grey
    d = pytesseract.image_to_data(Image.fromarray(big), config="--psm 11", output_type=pytesseract.Output.DICT)
    words = [dict(x0=d["left"][i] / up, y0=d["top"][i] / up, x1=(d["left"][i] + d["width"][i]) / up,
                  y1=(d["top"][i] + d["height"][i]) / up, t=d["text"][i].strip())
             for i in range(len(d["text"])) if re.search(r"[A-Za-z]{2,}", d["text"][i] or "") and float(d["conf"][i]) > 30]
    vocab = LITHOLOGY_WORDS + EXTRA_LITHOLOGY_WORDS
    # keep lithology words (and connectors) only: OCR also "reads" swatch patterns as junk words, which would glue the
    # swatch to its name and hide it
    words = [w for w in words if clean_label(w["t"], vocab) or w["t"].lower().strip(".,-") in ("or", "and", "&")]
    phrases = []
    for w in sorted(words, key=lambda w: w["x0"]):
        h = w["y1"] - w["y0"]
        for p in phrases:
            ph = p["y1"] - p["y0"]
            overlap = min(p["y1"], w["y1"]) - max(p["y0"], w["y0"])
            if overlap > 0.5 * min(h, ph) and -2 <= w["x0"] - p["x1"] < 1.3 * max(h, ph):
                p.update(x1=max(p["x1"], w["x1"]), y0=min(p["y0"], w["y0"]), y1=max(p["y1"], w["y1"]), t=p["t"] + " " + w["t"])
                break
        else:
            phrases.append(dict(w))
    # "content" = ink, or (colour legends) any clearly non-white pixel: coloured fills are part of the swatch
    if leg_rgb is not None:
        hsv = cv2.cvtColor(leg_rgb, cv2.COLOR_RGB2HSV)
        content = ((leg_rgb.min(2) < 200) | (hsv[..., 1] > 60)).astype(np.uint8) | leg_ink
    else:
        content = leg_ink
    text_mask = np.zeros_like(content)
    for p in phrases:
        text_mask[int(p["y0"]):int(np.ceil(p["y1"])) + 1, int(p["x0"]):int(np.ceil(p["x1"])) + 1] = 1
    out, seen = [], set()
    for p in sorted(phrases, key=lambda p: (p["y0"], p["x0"])):
        name = clean_label(p["t"], vocab)
        if not name or name in seen or name.lower() == "and" or re.search(r"unknown|sample", p["t"], re.I):
            continue
        h = max(4.0, p["y1"] - p["y0"])
        r0, r1 = max(0, int(p["y0"] - 0.5 * h)), min(H, int(np.ceil(p["y1"] + 0.5 * h)))
        band = content[r0:r1] & (1 - text_mask[r0:r1])
        cols = band.any(0)
        x = int(p["x0"]) - 1
        gap_max, inner_gap = max(6, int(1.5 * h)), max(3, int(0.5 * h))
        g = 0
        while x >= 0 and not cols[x] and g <= gap_max:             # gap between swatch and name
            x, g = x - 1, g + 1
        if x < 0 or not cols[x]:
            continue
        right, g = x, 0
        while x >= 0 and g <= inner_gap and right - x < 15 * h:    # the swatch itself (small internal gaps allowed)
            g = 0 if cols[x] else g + 1
            x -= 1
        left = x + g + 1
        rows = np.where(band[:, left:right + 1].any(1))[0]
        if not len(rows):
            continue
        y0, y1 = r0 + rows[0], r0 + rows[-1] + 1
        sw_w, sw_h = right + 1 - left, y1 - y0
        if sw_w < 0.8 * h or sw_h < 0.5 * h or content[y0:y1, left:right + 1].mean() < 0.15:
            continue
        m = 1 if min(sw_w, sw_h) < 20 else int(0.08 * min(sw_w, sw_h))
        inner = leg_ink[y0 + m: y1 - m, left + m: right + 1 - m]
        rgb = leg_rgb[y0 + m: y1 - m, left + m: right + 1 - m] if leg_rgb is not None else None
        if inner.size == 0 or (inner.mean() <= 0.005 and rgb is None):
            continue
        seen.add(name)
        out.append(Swatch(name=name, box=(int(left), int(y0), int(sw_w), int(sw_h)), ink=inner, rgb=rgb))
    return out


# ======================================================================================================================
# Stage 7 - Naming (cluster -> legend swatch)
# ======================================================================================================================
COMMONNESS = ["Shale", "Sandstone", "Sand And Sandstone", "Siltstone", "Limestone", "Dolomite", "Clay", "Claystone",
              "Mudstone", "Coal", "Marl", "Chalk", "Anhydrite", "Halite", "Gypsum"]   # tie-break order (most common first)


def lookalike_groups(swatches, describe: TextureDescriber) -> dict:
    """Swatches drawn (almost) identically: same stroke directions, similar ink density and, in colour, similar fill.
    Returns {i: set of indices that look like swatch i (including i)}."""
    idx = [TEXTURE_FEATURES.index(k) for k in DIRECTION_FEATURES]
    f = [describe(s.ink) for s in swatches]
    col = [fill_colour(s.rgb, s.ink) if s.rgb is not None else None for s in swatches]
    groups = {i: {i} for i in range(len(swatches))}
    for i in range(len(f)):
        for j in range(i + 1, len(f)):
            same_strokes = np.linalg.norm(f[i][idx] - f[j][idx]) < 0.05
            same_density = abs(f[i][0] - f[j][0]) < 0.25 * max(f[i][0], f[j][0], 1e-6)
            same_colour = col[i] is None or col[j] is None or np.linalg.norm(col[i] - col[j]) < 12
            if same_strokes and same_density and same_colour:
                groups[i].add(j)
                groups[j].add(i)
    return groups


def swatch_variants(sw: Swatch, width: int, scale: Scale, describe: TextureDescriber, cfg: Config) -> np.ndarray:
    """Feature vectors of a swatch resized x{0.35..1.5} and thickened by 0-3 px, tiled to column-window size."""
    colour = fill_colour(sw.rgb, sw.ink) if sw.rgb is not None else None
    rows = []
    for s in cfg.swatch_scales:
        im = cv2.resize(sw.ink, None, fx=s, fy=s, interpolation=cv2.INTER_NEAREST)
        for d in cfg.swatch_dilations:
            v = cv2.dilate(im, np.ones((d + 1, d + 1), np.uint8)) if d else im
            reps = (int(np.ceil(3 * scale.window / v.shape[0])) + 1, int(np.ceil(width / v.shape[1])) + 1)
            f = describe(np.tile(v, reps)[:3 * scale.window, :width])
            rows.append(np.concatenate([f, colour]) if colour is not None else f)
    return np.array(rows)


def name_clusters(cluster_feats: dict, swatches, F_all, width, scale, describe, groups, cfg: Config) -> dict:
    """Nearest swatch variant per cluster, using only the scale-invariant NAMING_FEATURES (+ fill colour for colour
    scans), standardised with the column's own feature statistics."""
    if not swatches:
        return {c: Match("Unknown", True, [], 0.0, {}) for c in cluster_feats}
    cols = [TEXTURE_FEATURES.index(k) for k in NAMING_FEATURES]
    cols += list(range(len(TEXTURE_FEATURES), F_all.shape[1]))         # colour columns, if any
    scaler = FlooredScaler([feature_names(F_all)[i] for i in cols]).fit(F_all[:, cols])
    names = [s.name for s in swatches]
    variants = [scaler.transform(swatch_variants(s, width, scale, describe, cfg)[:, cols]) for s in swatches]
    out = {}
    for c, f in cluster_feats.items():
        x = scaler.transform(f[cols][None])[0]
        d = np.array([np.linalg.norm(v - x, axis=1).min() for v in variants])
        order = np.argsort(d)
        best = int(order[0])
        margin = float((d[order[1]] - d[best]) / (d[best] + 1e-9)) if len(d) > 1 else 1.0
        lookalikes = set(groups.get(best, {best}))
        near = {i for i in range(len(d)) if d[i] <= (1 + cfg.tie_ratio) * d[best]}
        if len(lookalikes) > 1:
            # identical swatches: the pattern cannot decide, the commonness prior does
            rank = lambda i: COMMONNESS.index(names[i]) if names[i] in COMMONNESS else len(COMMONNESS)
            best = min(lookalikes, key=lambda i: (rank(i), d[i]))
        tied = lookalikes | near                                  # near-ties keep the nearest swatch, but are flagged
        out[c] = Match(name=names[best], ambiguous=len(tied) > 1,
                       alternatives=sorted({names[i] for i in tied} - {names[best]}), margin=round(margin, 4),
                       distances={names[i]: round(float(d[i]), 4) for i in order[:3]})
    return out


# ======================================================================================================================
# Stage 8 - Depth calibration
# ======================================================================================================================
@dataclass
class DepthFit:
    a: float                                     # depth = a * y + b   (y = full-image pixel row)
    b: float
    n_used: int
    n_read: int
    points: list

    def depth(self, y: float) -> float:
        return round(self.a * y + self.b, 2)


def calibrate_depth(image: Image.Image, box, cfg: Config) -> Optional[DepthFit]:
    """OCR the depth labels and fit depth = a*y + b with RANSAC.

    The labels are printed sideways, so the depth column is rotated 90 deg counter-clockwise (a label's x position
    in the rotated image is its y position on the page). Long columns are read in 8000-row strips (Tesseract's limit
    is 32767 px), at most depth_max_strips of them, evenly spaced. Every pair of labels proposes a line; the line most labels
    agree with (within depth_inlier_px) wins and is refitted on its inliers. OCR misreads never get a majority.
    """
    fit = _fit_depth(_read_depth_labels(image, box, cfg, upright=False), cfg)
    if cfg.depth_upright_fallback and (fit is None or fit.n_used < 3):
        # some logs print the labels upright and with thousands separators ("3,000"): read them that way too
        alt = _fit_depth(_read_depth_labels(image, box, cfg, upright=True), cfg)
        if alt is not None and (fit is None or alt.n_used > fit.n_used):
            fit = alt
    return fit


def _read_depth_labels(image: Image.Image, box, cfg: Config, upright: bool) -> list:
    """(y, depth) of every number OCR reads in the depth column. Sideways labels: rotate the column 90 deg CCW.
    Upright labels: read the column as is, upscaled x2 (small print), commas allowed."""
    x0, y0, x1, y1 = box
    pts = []
    # Tesseract cannot read images longer than 32767 px, and long logs reach 90 000+ px: read overlapping strips
    strip, overlap = 8000, 400
    starts = list(range(y0, y1, strip - overlap))
    if len(starts) > cfg.depth_max_strips:                       # a line needs few labels: read evenly spaced strips
        starts = [starts[int(round(i))] for i in np.linspace(0, len(starts) - 1, cfg.depth_max_strips)]
    up = 2 if upright else 1
    for s0 in starts:
        s1 = min(y1, s0 + strip)
        img = ((1 - to_ink(image.crop((x0, s0, x1, s1)), cfg)) * 255).astype(np.uint8)
        if upright:
            img = cv2.resize(img, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
            wl = "0123456789.,"
        else:
            img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
            wl = "0123456789."
        d = pytesseract.image_to_data(Image.fromarray(img), output_type=pytesseract.Output.DICT,
                                      config=f"--psm 11 -c tessedit_char_whitelist={wl}")
        for i, t in enumerate(d["text"]):
            t = t.strip().strip(".,")
            ok = re.fullmatch(r"\d{2,6}(\.\d+)?", t) or (upright and re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", t))
            if ok and float(d["conf"][i]) > 50:
                t = t.replace(",", "")
                yc = s0 + ((d["top"][i] + d["height"][i] / 2) / up if upright else d["left"][i] + d["width"][i] / 2)
                if all(abs(yc - q[0]) > 20 or float(t) != q[1] for q in pts):      # skip duplicates from overlaps
                    pts.append((yc, float(t)))
    return pts


def _fit_depth(pts: list, cfg: Config) -> Optional[DepthFit]:
    """RANSAC line depth = a*y + b through the OCR'd labels (see calibrate_depth)."""
    if len(pts) < 2:
        return None
    y, z = np.array(pts).T
    best = None
    for i in range(len(y)):
        for j in range(i + 1, len(y)):
            if abs(y[j] - y[i]) < 20 or z[j] == z[i]:
                continue
            a = (z[j] - z[i]) / (y[j] - y[i])
            if a <= 0:                                            # depth increases downwards
                continue
            inliers = np.abs((z - z[i]) / a + y[i] - y) < cfg.depth_inlier_px
            if best is None or inliers.sum() > best.sum():
                best = inliers
    if best is None or best.sum() < 2:
        return None
    a, b = np.polyfit(y[best], z[best], 1)
    return DepthFit(float(a), float(b), int(best.sum()), len(pts),
                    [dict(y=float(p), depth=float(q), used=bool(u)) for p, q, u in zip(y, z, best)])


# ======================================================================================================================
# Stage 9 - Evaluation (only when ground truth exists)
# ======================================================================================================================
def _rows_to_labels(intervals: list, ys: np.ndarray) -> np.ndarray:
    out = np.full(len(ys), None, dtype=object)
    for r in intervals:
        out[(ys >= r["top_y"]) & (ys < r["bottom_y"])] = r["lithology"]
    return out


def evaluate(pred: list, truth: list, tol_px: int = 10) -> dict:
    """row_acc: % of labelled pixel rows with the right rock (a prediction matches if the true name is one of its words,
    e.g. "Sandstone" in "Sand And Sandstone"); boundary error (mean/max px); boundary F1 within tol_px."""
    ys = np.arange(truth[0]["top_y"], truth[-1]["bottom_y"])
    t, p = _rows_to_labels(truth, ys), _rows_to_labels(pred, ys)
    acc = np.mean([a is not None and isinstance(b, str) and a.lower() in b.lower().split() for a, b in zip(t, p)])
    tb = np.array([r["top_y"] for r in truth[1:]])
    pb = np.array([r["top_y"] for r in pred[1:]])
    pb_in = pb[(pb > ys[0] + tol_px) & (pb < ys[-1] - tol_px)]
    err = [int(np.min(np.abs(pb - b))) for b in tb] if len(pb) and len(tb) else [float("nan")]
    used, tp = set(), 0
    for b in pb_in:
        j = [i for i in range(len(tb)) if i not in used and abs(tb[i] - b) <= tol_px]
        if j:
            used.add(j[0])
            tp += 1
    prec, rec = tp / max(1, len(pb_in)), tp / max(1, len(tb))
    conf = pd.crosstab(pd.Series(t, name="true"), pd.Series([x or "none" for x in p], name="pred"), normalize="index")
    return dict(row_acc=round(100 * float(acc), 2), boundary_mean_px=round(float(np.mean(err)), 2),
                boundary_max_px=float(np.max(err)), boundary_f1=round(100 * (2 * prec * rec / (prec + rec) if prec + rec else 0), 2),
                n_pred=int(len(pb_in) + 1), n_true=len(truth),
                confusion={a: {b: round(100 * float(v), 1) for b, v in row.items() if v > 0} for a, row in conf.iterrows()})


# ======================================================================================================================
# Stage 10 - Rendering
# ======================================================================================================================
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6d5bd0", "#8c8b82"]
BOX_COLOURS = {"depth_column": (255, 0, 0), "interpreted_lithology_column": (0, 160, 255), "lithology_legend": (255, 130, 0)}


class Colours:
    """Stable colour per lithology name across all images of a run."""

    def __init__(self):
        self.map: dict = {}

    def __call__(self, name: str) -> tuple:
        if name not in self.map:
            h = PALETTE[len(self.map) % len(PALETTE)]
            self.map[name] = tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))
        return self.map[name]


def _font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _scaled_rgb(img: Image.Image, box, scale: float, strip: int = 2000) -> Image.Image:
    """Crop + shrink a (possibly huge, 1-bit) image strip by strip, so it never exists at full size in RGB."""
    x0, y0, x1, y1 = box
    out = Image.new("RGB", (max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale))))
    for ys in range(y0, y1, strip):
        ye = min(ys + strip, y1)
        part = img.crop((x0, ys, x1, ye)).convert("RGB")
        top, bottom = round((ys - y0) * scale), round((ye - y0) * scale)
        if scale != 1.0:
            part = part.resize((out.width, max(1, bottom - top)), Image.Resampling.BOX)
        out.paste(part, (0, top))
    return out


def render(image: Image.Image, boxes: dict, intervals: list, colours: Colours, region=None, scale: float = 0.25) -> Image.Image:
    """Paint the prediction (organisers' style): bounding boxes, a coloured band per interval over the left half of the
    lithology column (the original pattern stays visible), a line + depth at every boundary, the cluster id (with '?'
    when the name is ambiguous) to the right, and a colour key on top. `region` limits the view to (x0, y0, x1, y1)."""
    x_off, y_off, x_end, y_end = region or (0, 0, image.width, image.height)
    view = _scaled_rgb(image, (x_off, y_off, x_end, y_end), scale)
    S = lambda x, y: ((x - x_off) * scale, (y - y_off) * scale)
    draw = ImageDraw.Draw(view)
    font = _font(12)
    for i, (name, (bx0, by0, bx1, by1)) in enumerate(boxes.items()):
        c = BOX_COLOURS.get(name, (0, 200, 0))
        (a, b), (e, f) = S(bx0, by0), S(bx1, by1)
        draw.rectangle([a, b, e - 1, f - 1], outline=c, width=2)
        draw.text((a + 6, max(b, 0) + 4 + 16 * i), name, fill=c, font=font)
    overlay = Image.new("RGBA", view.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    cb = boxes["interpreted_lithology_column"]
    db = boxes.get("depth_column", cb)
    cx0, _ = S(cb[0], 0)
    cx1, _ = S(cb[2], 0)
    dx, _ = S(db[0], 0)
    mid = cx0 + (cx1 - cx0) / 2
    for r in intervals:
        c = colours(r["lithology"])
        _, t = S(0, r["top_y"])
        _, btm = S(0, r["bottom_y"])
        od.rectangle([cx0, t, mid, btm], fill=c + (150,), outline=c + (255,))
        od.text((cx1 + 8, (t + btm) / 2), f"cluster {r['cluster_id']}" + (" ?" if r.get("ambiguous") else ""),
                fill=c + (255,), font=font, anchor="lm", stroke_width=3, stroke_fill=(255, 255, 255, 255))
        od.line([(dx, t), (cx1, t)], fill=(0, 40, 200, 255), width=1)
        if r.get("top_depth") is not None:
            od.text((dx - 6, t), f"{r['top_depth']:g}", fill=(0, 40, 200, 255), font=font, anchor="rm",
                    stroke_width=3, stroke_fill=(255, 255, 255, 255))
    view = Image.alpha_composite(view.convert("RGBA"), overlay).convert("RGB")
    names = list(dict.fromkeys(r["lithology"] for r in intervals))
    out = Image.new("RGB", (view.width, view.height + 34), "white")
    out.paste(view, (0, 34))
    kd = ImageDraw.Draw(out)
    x = 10
    for n in names:
        kd.rectangle([x, 8, x + 18, 26], fill=colours(n), outline=(0, 0, 0))
        kd.text((x + 26, 17), n, fill=(0, 0, 0), font=_font(15), anchor="lm")
        x += 46 + int(kd.textlength(n, font=_font(15)))
    return out


def plot_legend(swatches, groups, path: Path) -> Optional[str]:
    if not swatches:
        return None
    ncol = min(len(swatches), 8)                                 # wrap long legends onto several rows
    nrow = int(np.ceil(len(swatches) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(1.9 * ncol, 2.3 * nrow), squeeze=False)
    for ax in axes.flat[len(swatches):]:
        ax.axis("off")
    for i, (ax, s) in enumerate(zip(axes.flat, swatches)):
        ax.imshow(s.rgb if s.rgb is not None else s.ink, cmap=None if s.rgb is not None else "gray_r", vmin=0, vmax=1 if s.rgb is None else None)
        look = [swatches[j].name for j in groups[i] if j != i]
        ax.set_title(s.name + (f"\n≈ {', '.join(look)}" if look else ""), fontsize=8, color="#b3261e" if look else "#1f1f1e")
        ax.axis("off")
    plt.tight_layout()
    plt.savefig(path, dpi=110)
    plt.close(fig)
    return str(path)


def plot_depth(fit: Optional[DepthFit], path: Path) -> Optional[str]:
    if fit is None:
        return None
    used = [(p["y"], p["depth"]) for p in fit.points if p["used"]]
    rej = [(p["y"], p["depth"]) for p in fit.points if not p["used"]]
    zu = np.array([z for _, z in used])
    pad = max(1.0, 0.1 * (zu.max() - zu.min()))
    lo, hi = zu.min() - pad, zu.max() + pad
    fig, ax = plt.subplots(figsize=(5, 3.2))
    ax.scatter(*zip(*used), s=18, color="#2a78d6", label=f"labels used ({len(used)})", zorder=3)
    shown = [(y, z) for y, z in rej if lo <= z <= hi]
    if rej:
        ax.scatter(*(zip(*shown) if shown else ([], [])), s=22, color="#d0302c", marker="x", zorder=3,
                   label=f"rejected OCR reads ({len(rej)}, {len(rej) - len(shown)} off-scale)")
    xs = np.linspace(min(p["y"] for p in fit.points), max(p["y"] for p in fit.points), 2)
    ax.plot(xs, fit.a * xs + fit.b, color="#5c5b55", lw=1, label=f"depth = {fit.a:.5f}·y + {fit.b:.1f}")
    ax.set_ylim(lo, hi)
    ax.set_xlabel("image row y (px)")
    ax.set_ylabel("depth label")
    ax.grid(color="#e4e3dd", lw=.6)
    ax.legend(frameon=False, fontsize=7)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    plt.tight_layout()
    plt.savefig(path, dpi=110)
    plt.close(fig)
    return str(path)


# ======================================================================================================================
# Pipeline
# ======================================================================================================================
def check_tesseract() -> None:
    cmd = pytesseract.pytesseract.tesseract_cmd
    if shutil.which(cmd) is None and not Path(cmd).exists():
        raise RuntimeError("Tesseract OCR not found. Run ./setup_venv.sh (installs it inside .venv, no sudo), "
                           "or install it system-wide: sudo apt install tesseract-ocr")


def process_log(log: Log, out_dir: Path, cfg: Config = Config(), colours: Optional[Colours] = None,
                on_progress: ProgressFn = lambda e: None) -> dict:
    """Run the full pipeline on one log, write its outputs to out_dir, return a JSON-serialisable record."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    colours = colours or Colours()
    timings = {}
    t0 = time.perf_counter()
    tracemalloc.start()

    def lap(name):
        nonlocal t0
        timings[name] = round(time.perf_counter() - t0, 3)
        on_progress(dict(event="stage", log=log.stem, stage=name, seconds=timings[name]))
        t0 = time.perf_counter()

    image = Image.open(log.image_path)
    boxes = json.loads(Path(log.annotation_path).read_text())["bounding_boxes"]

    # 1-3: column, colour, scale
    ink, rgb, col_x0, col_y0 = prepare_column(image, boxes["interpreted_lithology_column"], cfg)
    scale = measure_period(ink, cfg)
    ink = remove_grid_lines(ink, scale, cfg)
    describe = TextureDescriber(scale)
    lap("column")

    # 4-5: features, clusters, beds
    F, centres, dens = window_features(ink, rgb, scale, describe, cfg)
    labels, X, centroids, silhouette = cluster_windows(F, cfg)
    labels, transitions = transition_guard(labels, X, centroids, scale)
    beds = refine_boundaries(ink, labels_to_beds(labels, centres, ink.shape[0], scale), scale, describe)
    lap("segmentation")

    # 6-7: legend and names
    leg_crop = image.crop(tuple(boxes["lithology_legend"]))
    leg_ink, leg_rgb = to_ink(leg_crop, cfg), (np.array(leg_crop.convert("RGB")) if is_colour(leg_crop) else None)
    swatches = find_swatches(leg_ink, leg_rgb)
    legend_method = "size-groups"
    if cfg.text_legend_fallback and swatches_look_wrong(swatches):
        alt = find_swatches_by_text(leg_ink, leg_rgb)
        if len(alt) > sum(len(s.name.split()) <= 4 for s in swatches):
            swatches, legend_method = alt, "text-anchored"
    groups = lookalike_groups(swatches, describe)
    rock = {int(c) for c in np.unique(labels) if np.median(dens[labels == c]) >= cfg.blank_density}
    cluster_feats = {c: np.median(F[labels == c], 0) for c in rock}
    matches = name_clusters(cluster_feats, swatches, F, ink.shape[1], scale, describe, groups, cfg)
    lap("legend+naming")

    # 8: depth
    fit = calibrate_depth(image, boxes["depth_column"], cfg) if "depth_column" in boxes else None
    lap("depth")

    intervals = []
    for c, top, bottom in beds:
        if c not in matches:
            continue                                               # blank paper
        m = matches[c]
        ty, by = top + col_y0, bottom + col_y0
        intervals.append(asdict(Interval(top_y=int(ty), bottom_y=int(by), cluster_id=int(c), lithology=m.name,
                                         top_depth=fit.depth(ty) if fit else None,
                                         bottom_depth=fit.depth(by) if fit else None, ambiguous=m.ambiguous)))
    if cfg.merge_same_name:                  # two clusters with the same name are one rock: no boundary between them
        merged = []
        for r in intervals:
            p = merged[-1] if merged else None
            if p and p["lithology"] == r["lithology"] != "Unknown" and p["bottom_y"] == r["top_y"]:
                p.update(bottom_y=r["bottom_y"], bottom_depth=r["bottom_depth"], ambiguous=p["ambiguous"] or r["ambiguous"])
            else:
                merged.append(r)
        intervals = merged
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # outputs
    csv_path = out_dir / f"{log.stem}.csv"
    pd.DataFrame(intervals, columns=["top_depth", "bottom_depth", "lithology", "top_y", "bottom_y", "cluster_id"]) \
        .to_csv(csv_path, index=False)
    page = out_dir / f"{log.stem}_page.png"
    render(image, boxes, intervals, colours, scale=cfg.page_scale).save(page)
    cb, db = boxes["interpreted_lithology_column"], boxes.get("depth_column", boxes["interpreted_lithology_column"])
    if cfg.column_full_width:                # whole page width: nothing is cut, whatever side the depth column is on
        region = (0, cb[1], image.width, cb[3])
    else:
        region = (max(0, min(cb[0], db[0]) - 150), cb[1], min(image.width, max(cb[2], db[2]) + 260), cb[3])
    column_png = out_dir / f"{log.stem}_column.png"
    render(image, {k: v for k, v in boxes.items() if k != "lithology_legend"}, intervals, colours, region,
           cfg.column_scale).save(column_png)
    legend_png = plot_legend(swatches, groups, out_dir / f"{log.stem}_legend.png")
    depth_png = plot_depth(fit, out_dir / f"{log.stem}_depth.png")
    lap("render")

    record = dict(
        stem=log.stem, image=str(log.image_path), colour_scan=rgb is not None,
        scale=asdict(scale), k=int(len(np.unique(labels))), silhouette=round(silhouette, 3),
        transition_clusters_removed=transitions, n_intervals=len(intervals),
        legend_method=legend_method,
        swatches=[dict(name=s.name, look_alikes=[swatches[j].name for j in groups[i] if j != i]) for i, s in enumerate(swatches)],
        clusters={str(c): asdict(m) for c, m in matches.items()},
        ambiguous=sorted({r["lithology"] for r in intervals if r["ambiguous"]}),
        depth=None if fit is None else dict(a=fit.a, b=fit.b, n_used=fit.n_used, n_read=fit.n_read),
        intervals=intervals, timings_s=timings, runtime_s=round(sum(timings.values()) - timings["render"], 3),
        peak_mb=round(peak / 1e6, 1),
        files=dict(csv=str(csv_path), page_png=str(page), column_png=str(column_png), legend_png=legend_png,
                   depth_png=depth_png),
    )
    if log.labels_path:
        record["metrics"] = evaluate(intervals, pd.read_csv(log.labels_path).to_dict("records"))
    on_progress(dict(event="log_done", log=log.stem, n_intervals=len(intervals), runtime_s=record["runtime_s"],
                     metrics=record.get("metrics"), ambiguous=record["ambiguous"]))
    return record


IMAGE_SUFFIXES = (".tif", ".tiff", ".png", ".jpg", ".jpeg")


def find_logs(data_dir: Path, labels_dir: Optional[Path] = None) -> list:
    """Find every log under data_dir. Two layouts are supported:

        flat:     data_dir/<stem>.json + data_dir/<stem>.tif
        dataset:  data_dir/bbox_annotation/<stem>.json + data_dir/images/<stem>.tif   (the organisers' layout;
                  other folders such as bbox_vis/ are ignored)

    The image comes from the JSON's image_name (or <stem>.tif/.png/.jpg). Labels, if present: labels_dir/<stem>.csv,
    data_dir/labels/<stem>.csv or data_dir/<stem>.csv."""
    data_dir = Path(data_dir)
    labels_dir = Path(labels_dir) if labels_dir else data_dir / "labels"
    json_dirs = [data_dir] + [d for d in sorted(data_dir.iterdir()) if d.is_dir() and "annotation" in d.name.lower()]
    image_dirs = [data_dir] + [d for d in sorted(data_dir.iterdir()) if d.is_dir() and d.name.lower() in ("images", "image", "imgs")]
    logs = []
    for jp in sorted(p for d in json_dirs for p in d.glob("*.json")):
        try:
            ann = json.loads(jp.read_text())
        except json.JSONDecodeError:
            continue
        if "bounding_boxes" not in ann:
            continue
        cands = [d / ann["image_name"] for d in [jp.parent] + image_dirs if ann.get("image_name")]
        cands += [p for d in [jp.parent] + image_dirs for p in sorted(d.glob(jp.stem + ".*"))
                  if p.suffix.lower() in IMAGE_SUFFIXES]
        img = next((p for p in cands if p.is_file()), None)
        if img is None:
            continue
        gt = next((p for p in (labels_dir / f"{jp.stem}.csv", data_dir / f"{jp.stem}.csv") if p.is_file()), None)
        logs.append(Log(jp.stem, img, jp, gt))
    return logs


EXAMPLE_STEM = "0000000009_d-582_0000020435-0001_log06_fe_5in_md"


def run_folder(data_dir, out_dir, labels_dir=None, cfg: Config = Config(), on_progress: ProgressFn = lambda e: None) -> dict:
    """Process every log in data_dir; write outputs and summary.json to out_dir; return the summary."""
    check_tesseract()
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    labels_dir = Path(labels_dir) if labels_dir else data_dir / "labels"
    logs = find_logs(data_dir, labels_dir)
    # the organisers' example.csv (in data_dir or data_dir/example/) is the ground truth of the example log
    example_csv = next((p for p in (data_dir / "example.csv", data_dir / "example" / "example.csv") if p.is_file()), None)
    if example_csv and any(l.stem == EXAMPLE_STEM and l.labels_path is None for l in logs):
        labels_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(example_csv, labels_dir / f"{EXAMPLE_STEM}.csv")
        logs = find_logs(data_dir, labels_dir)
    if not logs:
        raise FileNotFoundError(f"no logs in {data_dir}: need <stem>.json (with bounding_boxes) + its image")
    on_progress(dict(event="start", n_logs=len(logs)))
    t0 = time.perf_counter()
    colours = Colours()
    records, errors = [], []
    for i, log in enumerate(logs, 1):
        on_progress(dict(event="log_start", log=log.stem, index=i, total=len(logs)))
        try:
            records.append(process_log(log, out_dir, cfg, colours, on_progress))
        except Exception as e:                                     # one bad log must not stop the batch
            errors.append(dict(log=log.stem, error=f"{type(e).__name__}: {e}"))
            on_progress(dict(event="log_error", log=log.stem, error=str(e)))
    labelled = [r["metrics"] for r in records if "metrics" in r]
    summary = dict(
        created=time.strftime("%Y-%m-%d %H:%M:%S"), data_dir=str(data_dir), n_logs=len(logs), n_errors=len(errors),
        total_s=round(time.perf_counter() - t0, 2), config=asdict(cfg), errors=errors, logs=records,
        mean_metrics=None if not labelled else {k: round(float(np.mean([m[k] for m in labelled])), 2)
                                                for k in ("row_acc", "boundary_mean_px", "boundary_max_px", "boundary_f1")},
    )
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    on_progress(dict(event="done", total_s=summary["total_s"], n_errors=len(errors), mean_metrics=summary["mean_metrics"]))
    return summary


# ======================================================================================================================
# Command line
# ======================================================================================================================
def _print(e: dict) -> None:
    ev = e["event"]
    if ev == "log_start":
        print(f"[{e['index']}/{e['total']}] {e['log']}")
    elif ev == "log_done":
        m = e.get("metrics")
        acc = f"  row accuracy {m['row_acc']:.1f}%  boundary error {m['boundary_mean_px']:.1f}px" if m else ""
        amb = f"  ambiguous: {', '.join(e['ambiguous'])}" if e["ambiguous"] else ""
        print(f"      {e['n_intervals']} intervals  {e['runtime_s']:.1f}s{acc}{amb}")
    elif ev == "log_error":
        print(f"      ERROR: {e['error']}")
    elif ev == "done":
        mm = e["mean_metrics"]
        print(f"\ndone in {e['total_s']}s, errors: {e['n_errors']}"
              + (f", mean row accuracy {mm['row_acc']}%, mean boundary error {mm['boundary_mean_px']}px" if mm else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Extract the interpreted lithology column of well-log scans.")
    ap.add_argument("data_dir", help="folder with <stem>.json + image, or a dataset folder with bbox_annotation/ + "
                                     "images/ (optionally example.csv and labels/<stem>.csv for accuracy)")
    ap.add_argument("--out", default="outputs", help="output folder (default: outputs)")
    ap.add_argument("--labels", default=None, help="ground-truth folder with <stem>.csv (default: <data_dir>/labels)")
    ap.add_argument("--fixed-scale", action="store_true", help="do not measure the pattern period; use 25 px")
    a = ap.parse_args(argv)
    s = run_folder(a.data_dir, a.out, a.labels, Config(auto_scale=not a.fixed_scale), on_progress=_print)
    print(f"outputs: {Path(a.out).resolve()}  (summary.json, <stem>.csv, <stem>_page/_column/_legend/_depth.png)")
    return 1 if s["n_errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
