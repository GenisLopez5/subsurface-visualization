#!/usr/bin/env python3
"""
lithology_pipeline.py: extract the interpreted lithology column of scanned well logs as structured data.

The engine is the repository's `lithology/` package (RapidOCR + legend fingerprints). This module adapts it to the web
app (webapp/) and the command line: same inputs (image + bounding-box JSON), same outputs per log in out_dir:
    * <stem>.csv              one row per interval: top_depth, bottom_depth, lithology, top_y, bottom_y, cluster_id
    * <stem>_page.png         the whole page painted (boxes, intervals, depths, cluster ids)
    * <stem>_column.png       the column view (full page width) with the prediction painted on
    * <stem>_legend.png       the legend swatches that were found and the names OCR read
    * <stem>_depth.png        the depth labels OCR read and the fitted pixel -> depth line
    * <stem>_annotated.tif    high-resolution result in the challenge notebook's style (lithology/drawing.py)
and one summary.json for the whole folder (plus metrics when ground-truth labels are available).
Methods: "hybrid" (default), "lithology" (lithology/ package only), "cv" (cv_pipeline.py only).

How it works (method "hybrid", hybrid_pipeline.py; the default "hybrid_v2" adds colour mode, see hybrid_v2.py)
----------------------------------------------------------------------------------------
 1. Column        crop the lithology column, strip its frame and track borders, blank isolated grid lines (cv).
 2. Scale         measure the vertical pattern period and size every window and kernel from it (cv).
 3. Clusters      describe sliding windows by stroke directions, blobs, density and fill colour; k-means with k by
                  silhouette; a guard removes "transition" clusters; boundaries snap to the exact row (cv).
 4. Legend        fast Tesseract swatch finder first; if it looks wrong, RapidOCR legend reader (lithology/ package,
                  robust on mud logs); then a text-anchored fallback.
 5. Naming        each cluster is matched to the legend against resized and thickened copies of every swatch, so a
                  column drawn at another size or stroke weight still matches; identical swatches use a commonness
                  prior and are flagged ambiguous, like near-ties.
 6. Depth         Tesseract reads sideways or upright ("3,000") labels in seconds; if too few, RapidOCR reads them in
                  any orientation (a few chunks first) and fits a line, piecewise when needed.
 7. Merge         neighbouring intervals with the same name become one.
 8. Output        CSV in the organisers' format, painted PNGs, legend and depth plots, the notebook-style TIF, metrics.

Other methods: "lithology" (lithology/extract.py only: RapidOCR legend + per-window fingerprint matching) and "cv"
(cv_pipeline.py only).

Usage
-----
    .venv/bin/python lithology_pipeline.py data/ --out outputs/ [--labels labels/] [--method cv]

    from lithology_pipeline import run_folder, Config      # as a library (used by webapp/worker.py)
    summary = run_folder("data", "outputs", on_progress=print)
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.metadata
import io
import json
import shutil
import sys
import time
import tracemalloc
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd
from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parent                                            # the repository root, which holds lithology/
for p in (str(HERE), str(REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

import cv_pipeline as cv                                      # noqa: E402  (method "cv" + shared rendering/metrics)
from lithology import drawing as LD                           # noqa: E402
from lithology import extract as LX                           # noqa: E402

Image.MAX_IMAGE_PIXELS = None
ProgressFn = Callable[[dict], None]
Log = cv.Log
EXAMPLE_STEM = cv.EXAMPLE_STEM
IMAGE_SUFFIXES = (".tif", ".tiff", ".png", ".jpg", ".jpeg", ".pdf")
STAGES = {"lithology": ["column", "legend+naming", "segmentation", "depth", "render"],
          "cv": ["column", "segmentation", "legend+naming", "depth", "render"],
          "hybrid": ["column", "segmentation", "legend+naming", "depth", "render"],
          "hybrid_v2": ["column", "legend+naming", "segmentation", "depth", "render"]}
CHOICES = {"method": ["hybrid_v2", "hybrid", "lithology", "cv"]}                     # settings with a fixed set of values (a select in the UI)


# ======================================================================================================================
# Configuration
# ======================================================================================================================
@dataclass
class Config:
    """All settings. The web app builds its settings form from this class (the comments become the help text)."""
    # --- method -------------------------------------------------------------------------------------------------------
    method: str = "hybrid_v2"           # "hybrid_v2" = revised hybrid (colour mode, blank fix, legend quality; hybrid_v2.py); "hybrid" = first hybrid; "lithology" = lithology/ package only; "cv" = clustering only
    # --- hybrid method ------------------------------------------------------------------------------------------------
    hybrid_prefer_rapidocr: bool = False  # read the legend with RapidOCR first (slower; default: Tesseract first, RapidOCR if it looks wrong)
    hybrid_depth_min_labels: int = 4    # fewer Tesseract depth labels than this -> also try RapidOCR
    hybrid_colour_mode: bool = True     # hybrid_v2: read colour logs row by row by fill colour (thin colour bands)
    # --- lithology method ---------------------------------------------------------------------------------------------
    window_px: int = 0                  # classification window height in px; 0 = median legend swatch height (8-40)
    ambiguous_conf: float = 0.15        # intervals whose confidence is below this are flagged ambiguous
    keep_blank: bool = False            # also output intervals where the column is empty ("Blank")
    round_depth: float = 0.1            # round depths to this step (0 = no rounding)
    depth_chunk: int = 1600             # depth column is OCR'd in chunks of this many rows
    depth_max_chunks: int = 12          # at most this many evenly spaced chunks (a line needs few labels; 40 = slow)
    annotated_tif: bool = True          # also write <stem>_annotated.tif in the challenge notebook's style
    # --- output -------------------------------------------------------------------------------------------------------
    page_scale: float = 0.25            # size of <stem>_page.png relative to the scan
    column_scale: float = 0.5           # size of <stem>_column.png relative to the scan
    column_full_width: bool = True      # <stem>_column.png shows the whole page width (else only the two columns)
    # --- cv method: scale ---------------------------------------------------------------------------------------------
    auto_scale: bool = True             # measure P per log; if False, use default_period
    default_period: float = 25.0        # px, used when P cannot be measured
    period_range: tuple = (8.0, 80.0)   # plausible P (px); measurements outside are ignored
    # --- cv method: windows & segmentation (in periods) ---------------------------------------------------------------
    window_periods: float = 2.9         # window height (72 px at P=25)
    stride_fraction: float = 1 / 9      # window step as a fraction of the window height
    min_bed_periods: float = 2.4        # beds thinner than this are merged into a neighbour
    smooth_windows: int = 7             # median filter length along depth (in windows)
    k_range: tuple = (2, 8)             # number of clusters tried; chosen by silhouette score
    single_rock_silhouette: float = 0.2  # below this, the column is treated as one rock (k = 1)
    refine_radius_periods: float = 1.9  # boundary refinement search radius
    refine_window_periods: float = 1.6  # rows compared above/below a candidate cut
    # --- cv method: legend & naming -----------------------------------------------------------------------------------
    tie_ratio: float = 0.10             # swatches within 10% of the best distance are a tie
    text_legend_fallback: bool = True   # if few/garbled swatches: find swatches next to the OCR'd names instead
    merge_same_name: bool = True        # merge neighbouring intervals that got the same lithology name
    depth_upright_fallback: bool = True  # if sideways labels give < 3 inliers, also read upright labels ("3,000")


def cv_config(cfg: Config) -> cv.Config:
    """The cv method's own Config, from the shared settings (fields with the same name)."""
    names = {f.name for f in fields(cv.Config)}
    return cv.Config(**{k: v for k, v in asdict(cfg).items() if k in names})


# ======================================================================================================================
# OCR availability
# ======================================================================================================================
def ocr_info(method: str = "lithology") -> dict:
    """{'ok', 'version', 'path' or 'message'} for the OCR engine the method needs."""
    if method in ("cv", "hybrid", "hybrid_v2"):                                  # hybrid: Tesseract first, RapidOCR optional
        try:
            cv.check_tesseract()
            return dict(ok=True, version="Tesseract", path=str(cv.pytesseract.pytesseract.tesseract_cmd))
        except RuntimeError as e:
            return dict(ok=False, message=str(e))
    try:
        import rapidocr  # noqa: F401
        import onnxruntime  # noqa: F401
        return dict(ok=True, version=f"RapidOCR {importlib.metadata.version('rapidocr')}", path=rapidocr.__file__)
    except Exception as e:                                          # noqa: BLE001
        return dict(ok=False, message=f"RapidOCR not installed ({e}). Run: .venv/bin/pip install -r requirements.txt")


def check_ocr(method: str) -> None:
    info = ocr_info(method)
    if not info["ok"]:
        raise RuntimeError(info["message"])


# ======================================================================================================================
# Method "lithology": one log
# ======================================================================================================================
def _quiet():
    """lithology/extract.py and RapidOCR print progress; keep the worker's output clean."""
    return contextlib.redirect_stderr(io.StringIO())


def _is_colour(img: Image.Image, box) -> bool:
    x0, y0, x1, y1 = box
    return cv.is_colour(img.crop((x0, y0, x1, min(y1, y0 + 4000))))


def plot_legend(img: Image.Image, entries: list, path: Path) -> Optional[str]:
    """Grid of the swatches found in the legend with the label OCR read (ignored ones in grey)."""
    if not entries:
        return None
    import matplotlib.pyplot as plt
    ncol = min(len(entries), 6)
    nrow = int(np.ceil(len(entries) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(2.2 * ncol, 1.6 * nrow), squeeze=False)
    for ax in axes.flat:
        ax.axis("off")
    for ax, e in zip(axes.flat, entries):
        x0, y0, x1, y1 = e["swatch_bbox"]
        ax.imshow(img.crop((x0, y0, x1, y1)).convert("RGB"))
        ignored = e.get("ignored")
        ax.set_title(e["label"] + (f"\n({ignored})" if ignored else ""), fontsize=8,
                     color="#9c9b94" if ignored else "#1f1f1e")
    plt.tight_layout()
    plt.savefig(path, dpi=110)
    plt.close(fig)
    return str(path)


def process_log_lithology(log: Log, out_dir: Path, cfg: Config, colours: cv.Colours, on_progress: ProgressFn) -> dict:
    timings, t0 = {}, time.perf_counter()
    tracemalloc.start()

    def lap(name):
        nonlocal t0
        timings[name] = round(time.perf_counter() - t0, 3)
        on_progress(dict(event="stage", log=log.stem, stage=name, seconds=timings[name]))
        t0 = time.perf_counter()

    ann = json.loads(Path(log.annotation_path).read_text())
    boxes = ann["bounding_boxes"]
    ann.setdefault("width", None)
    image = LX.load_page(Path(log.image_path), ann)
    cb = boxes["interpreted_lithology_column"]
    colour_scan = _is_colour(image, cb)
    lap("column")

    with _quiet():
        entries = LX.parse_legend(image, boxes["lithology_legend"])
    lap("legend+naming")

    with _quiet():
        runs = LX.classify_column(image, cb, entries, win=cfg.window_px or None) if entries else []
    lap("segmentation")

    y_to_depth, calib, depth_error = None, None, None
    if "depth_column" in boxes:
        try:
            with _quiet():
                y_to_depth, calib = LX.calibrate_depth(image, boxes["depth_column"], chunk=cfg.depth_chunk,
                                                       max_chunks=cfg.depth_max_chunks)
        except RuntimeError as e:                                   # no readable labels: depths stay empty
            depth_error = str(e)
    lap("depth")

    def depth(y):
        if y_to_depth is None:
            return None
        v = float(y_to_depth(y))
        return round(float(np.round(v / cfg.round_depth) * cfg.round_depth), 4) if cfg.round_depth else round(v, 4)

    ids, intervals, conf_by_name = {}, [], {}
    for name, top, bottom, conf in runs:
        if name == LX.BLANK and not cfg.keep_blank:
            continue
        name = "Blank" if name == LX.BLANK else name
        cid = ids.setdefault(name, len(ids))
        conf_by_name.setdefault(name, []).append((conf, bottom - top))
        intervals.append(dict(top_y=int(top), bottom_y=int(bottom), cluster_id=cid, lithology=name,
                              top_depth=depth(top), bottom_depth=depth(bottom), ambiguous=bool(conf < cfg.ambiguous_conf),
                              confidence=round(float(conf), 3)))
    clusters = {}
    for name, cid in ids.items():
        c = conf_by_name[name]
        mean = float(np.average([x for x, _ in c], weights=[max(1, w) for _, w in c]))
        clusters[str(cid)] = dict(name=name, ambiguous=bool(mean < cfg.ambiguous_conf), alternatives=[],
                                  margin=round(mean, 4), distances={})
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # outputs
    out_dir = Path(out_dir)
    csv_path = out_dir / f"{log.stem}.csv"
    pd.DataFrame(intervals, columns=["top_depth", "bottom_depth", "lithology", "top_y", "bottom_y", "cluster_id"]) \
        .to_csv(csv_path, index=False)
    page = out_dir / f"{log.stem}_page.png"
    cv.render(image, boxes, intervals, colours, scale=cfg.page_scale).save(page)
    db = boxes.get("depth_column", cb)
    if cfg.column_full_width:
        region = (0, cb[1], image.width, cb[3])
    else:
        region = (max(0, min(cb[0], db[0]) - 150), cb[1], min(image.width, max(cb[2], db[2]) + 260), cb[3])
    column_png = out_dir / f"{log.stem}_column.png"
    cv.render(image, {k: v for k, v in boxes.items() if k != "lithology_legend"}, intervals, colours, region,
              cfg.column_scale).save(column_png)
    legend_png = plot_legend(image, entries, out_dir / f"{log.stem}_legend.png")
    fit = None
    if calib:
        fit = cv.DepthFit(calib["depth_per_px"], calib["intercept"], calib["n_labels_used"],
                          calib["n_labels_used"] + calib["n_labels_rejected"], calib["labels"])
    depth_png = cv.plot_depth(fit, out_dir / f"{log.stem}_depth.png")
    tif_path, tif_error = None, None
    if cfg.annotated_tif and intervals and calib and "depth_column" in boxes:
        try:
            tif_path = str(LD.render_tif(image, log.annotation_path, dict(intervals=intervals),
                                         out_dir / f"{log.stem}_annotated.tif"))
        except Exception as e:                                      # noqa: BLE001  (e.g. too large)
            tif_error = f"{type(e).__name__}: {e}"
    lap("render")

    record = dict(
        stem=log.stem, image=str(log.image_path), method="lithology", colour_scan=colour_scan, scale=None,
        window_px=cfg.window_px or None, k=len(ids), silhouette=None, transition_clusters_removed=[],
        n_intervals=len(intervals), legend_method="ocr-labels (lithology/extract.py)",
        swatches=[dict(name=e["label"], look_alikes=[], ignored=e.get("ignored"), raw_text=e.get("raw_text"))
                  for e in entries],
        clusters=clusters, ambiguous=sorted({r["lithology"] for r in intervals if r["ambiguous"]}),
        depth=None if calib is None else dict(a=calib["depth_per_px"], b=calib["intercept"], n_used=calib["n_labels_used"],
                                              n_read=calib["n_labels_used"] + calib["n_labels_rejected"],
                                              model=calib["model"], rms_px=round(calib["rms_residual_px"], 2)),
        depth_error=depth_error, intervals=intervals, timings_s=timings,
        runtime_s=round(sum(timings.values()) - timings["render"], 3), peak_mb=round(peak / 1e6, 1),
        files=dict(csv=str(csv_path), page_png=str(page), column_png=str(column_png), legend_png=legend_png,
                   depth_png=depth_png, annotated_tif=tif_path),
        annotated_tif_error=tif_error,
    )
    if log.labels_path:
        record["metrics"] = cv.evaluate(intervals, pd.read_csv(log.labels_path).to_dict("records"))
    return record


def process_log(log: Log, out_dir: Path, cfg: Config = Config(), colours: Optional[cv.Colours] = None,
                on_progress: ProgressFn = lambda e: None) -> dict:
    """Run one log with the configured method; write its outputs; return a JSON-serialisable record."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    colours = colours or cv.Colours()
    if cfg.method == "hybrid_v2":
        import hybrid_v2
        record = hybrid_v2.process_log_v2(log, out_dir, cfg, cv_config(cfg), colours, on_progress)
    elif cfg.method == "hybrid":
        import hybrid_pipeline
        record = hybrid_pipeline.process_log_hybrid(log, out_dir, cfg, cv_config(cfg), colours, on_progress)
    elif cfg.method == "cv":
        record = cv.process_log(log, out_dir, cv_config(cfg), colours, lambda e: e.get("event") != "log_done" and on_progress(e))
        record["method"] = "cv"
    else:
        record = process_log_lithology(log, out_dir, cfg, colours, on_progress)
    on_progress(dict(event="log_done", log=log.stem, n_intervals=record["n_intervals"], runtime_s=record["runtime_s"],
                     metrics=record.get("metrics"), ambiguous=record["ambiguous"]))
    return record


# ======================================================================================================================
# Folder
# ======================================================================================================================
def find_logs(data_dir: Path, labels_dir: Optional[Path] = None) -> list:
    """Every log under data_dir: flat (<stem>.json + image) or the organisers' layout (bbox_annotation/ + images/).
    The image comes from the JSON's image_name (or <stem>.tif/.png/.jpg/.pdf). Labels: labels_dir/<stem>.csv,
    data_dir/labels/<stem>.csv or data_dir/<stem>.csv."""
    data_dir = Path(data_dir)
    labels_dir = Path(labels_dir) if labels_dir else data_dir / "labels"
    json_dirs = [data_dir] + [d for d in sorted(data_dir.iterdir()) if d.is_dir() and "annotation" in d.name.lower()]
    image_dirs = [data_dir] + [d for d in sorted(data_dir.iterdir()) if d.is_dir() and d.name.lower() in ("images", "image", "imgs")]
    logs = []
    for jp in sorted(p for d in json_dirs for p in d.glob("*.json")):
        try:
            ann = json.loads(jp.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(ann, dict) or "bounding_boxes" not in ann:
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


def run_folder(data_dir, out_dir, labels_dir=None, cfg: Config = Config(), on_progress: ProgressFn = lambda e: None) -> dict:
    """Process every log in data_dir; write outputs and summary.json to out_dir; return the summary."""
    check_ocr(cfg.method)
    data_dir, out_dir = Path(data_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    labels_dir = Path(labels_dir) if labels_dir else data_dir / "labels"
    logs = find_logs(data_dir, labels_dir)
    example_csv = next((p for p in (data_dir / "example.csv", data_dir / "example" / "example.csv") if p.is_file()), None)
    if example_csv and any(l.stem == EXAMPLE_STEM and l.labels_path is None for l in logs):
        labels_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(example_csv, labels_dir / f"{EXAMPLE_STEM}.csv")
        logs = find_logs(data_dir, labels_dir)
    if not logs:
        raise FileNotFoundError(f"no logs in {data_dir}: need <stem>.json (with bounding_boxes) + its image")
    on_progress(dict(event="start", n_logs=len(logs), method=cfg.method, stages=STAGES[cfg.method]))
    t0 = time.perf_counter()
    colours = cv.Colours()
    records, errors = [], []
    for i, log in enumerate(logs, 1):
        on_progress(dict(event="log_start", log=log.stem, index=i, total=len(logs)))
        try:
            records.append(process_log(log, out_dir, cfg, colours, on_progress))
        except Exception as e:                                      # noqa: BLE001  one bad log must not stop the batch
            if tracemalloc.is_tracing():
                tracemalloc.stop()
            errors.append(dict(log=log.stem, error=f"{type(e).__name__}: {e}"))
            on_progress(dict(event="log_error", log=log.stem, error=f"{type(e).__name__}: {e}"))
    labelled = [r["metrics"] for r in records if "metrics" in r]
    summary = dict(
        created=time.strftime("%Y-%m-%d %H:%M:%S"), data_dir=str(data_dir), method=cfg.method, n_logs=len(logs),
        n_errors=len(errors), total_s=round(time.perf_counter() - t0, 2), config=asdict(cfg), errors=errors, logs=records,
        mean_metrics=None if not labelled else {k: round(float(np.mean([m[k] for m in labelled])), 2)
                                                for k in ("row_acc", "boundary_mean_px", "boundary_max_px", "boundary_f1")},
    )
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    on_progress(dict(event="done", total_s=summary["total_s"], n_errors=len(errors), mean_metrics=summary["mean_metrics"]))
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Extract the interpreted lithology column of well-log scans.")
    ap.add_argument("data_dir", help="folder with <stem>.json + image, or a dataset folder with bbox_annotation/ + images/")
    ap.add_argument("--out", default="outputs", help="output folder (default: outputs)")
    ap.add_argument("--labels", default=None, help="ground-truth folder with <stem>.csv (default: <data_dir>/labels)")
    ap.add_argument("--method", default="hybrid_v2", choices=CHOICES["method"])
    a = ap.parse_args(argv)
    s = run_folder(a.data_dir, a.out, a.labels, Config(method=a.method), on_progress=cv._print)
    print(f"outputs: {Path(a.out).resolve()}")
    return 1 if s["n_errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
