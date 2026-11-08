"""
Batch mode: every log of a folder.

FOLDER holds two sub-folders, one with the log images (.tif / .tiff / .pdf / .png) and one with
the annotation JSONs. Their names don't matter: they are recognised by what they contain. Each
JSON is paired with its image by the JSON's `image_name`, or failing that by file name.

Output:
    FOLDER/results/<log>.csv     intervals, same columns as example.csv (cluster_id included)
    FOLDER/results/<log>.tif     the log drawn by the notebook's visualize_results()
    FOLDER/results/summary.csv   one row per log: status, number of intervals, depth fit, error
    FOLDER/debug/<log>.json      full result for debugging: depth labels read, legend entries, confidences

Logs that already have results are skipped, so an interrupted batch continues where it stopped.
"""
from __future__ import annotations

import contextlib
import csv
import json
import traceback
from pathlib import Path

from . import pipeline
from .annotations import PAGE_EXT, IMAGE_EXT, load_annotation, norm

RESULTS_NAME = "results"
DEBUG_NAME = "debug"
SUMMARY_COLS = ["log", "status", "intervals", "top_depth", "bottom_depth", "px_per_depth_unit", "depth_labels_used",
                "depth_fit_rms_px", "legend_entries", "header_date", "lat", "lon", "seconds", "error", "image", "json"]


def header_cols(header: dict | None) -> dict:
    """Flatten the header (date + location) into summary.csv columns."""
    loc = (header or {}).get("location") or {}
    return {"header_date": (header or {}).get("date") or "", "lat": loc.get("lat", ""), "lon": loc.get("lon", "")}


def _annotations_in(d: Path):
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append((p, load_annotation(p)))
        except Exception:
            continue
    return out


def _images_in(d: Path):
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT)


def scan_folder(folder) -> dict:
    """Find the JSON folder and the image folder inside `folder` and pair their files."""
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError(f"{folder} is not a folder")
    subdirs = [d for d in sorted(folder.iterdir())
               if d.is_dir() and d.name not in (RESULTS_NAME, DEBUG_NAME) and not d.name.startswith(".")]
    cands = subdirs + [folder]  # also accept both kinds of files directly in the folder

    ann_dir = max(cands, key=lambda d: len(_annotations_in(d)), default=None)
    if ann_dir is None or not _annotations_in(ann_dir):
        raise ValueError(f"No annotation JSONs found in {folder}")

    def image_score(d):  # prefer page scans (.tif/.pdf) over preview .pngs
        imgs = _images_in(d)
        return (sum(p.suffix.lower() in PAGE_EXT for p in imgs), len(imgs))

    img_cands = [d for d in cands if d != ann_dir] or [ann_dir]
    img_dir = max(img_cands, key=image_score)
    if image_score(img_dir) == (0, 0):
        img_dir = ann_dir if _images_in(ann_dir) else None
    if img_dir is None:
        raise ValueError(f"No image folder found in {folder}")

    images = _images_in(img_dir)
    by_name = {p.name: p for p in images}
    by_norm = {}
    for p in images:
        for k in (norm(p.name), norm(p.stem), norm(Path(p.stem).stem)):
            by_norm.setdefault(k, p)

    pairs, missing, used = [], [], set()
    for jp, ann in _annotations_in(ann_dir):
        name = ann.get("image_name", "")
        img = by_name.get(name)
        if img is None:
            for k in (norm(name), norm(Path(name).stem), norm(jp.stem), norm(Path(jp.stem).stem)):
                if k in by_norm:
                    img = by_norm[k]
                    break
        if img is None:
            missing.append(jp.name)
        else:
            pairs.append({"name": jp.stem, "json": str(jp), "image": str(img)})
            used.add(img)
    results, debug = folder / RESULTS_NAME, folder / DEBUG_NAME
    for p in pairs:
        p["done"] = all(f.exists() for f in (results / f"{p['name']}.csv", results / f"{p['name']}.tif",
                                             debug / f"{p['name']}.json"))
    return {
        "folder": str(folder),
        "json_folder": str(ann_dir),
        "image_folder": str(img_dir),
        "results_folder": str(results),
        "debug_folder": str(debug),
        "pairs": pairs,
        "json_without_image": missing,
        "images_without_json": [p.name for p in images if p not in used and p.suffix.lower() in PAGE_EXT],
    }


def write_summary(rows, out_dir: Path):
    with open(out_dir / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def run_batch(scan: dict, cache_dir: Path, tmp_root: Path, force=False, log=print, on_item=None,
              should_stop=lambda: False, item_lock=None):
    """Process every pair of a scan_folder() result. on_item(i, row) is called as each log changes."""
    out_dir, debug_dir = Path(scan["results_folder"]), Path(scan["debug_folder"])
    out_dir.mkdir(exist_ok=True)
    rows = []
    for i, pair in enumerate(scan["pairs"]):
        if should_stop():
            log("stopped")
            break
        name = pair["name"]
        log(f"[{i + 1}/{len(scan['pairs'])}] {name}")
        base = {"log": name, "image": Path(pair["image"]).name, "json": Path(pair["json"]).name}
        if on_item:
            on_item(i, {**base, "status": "running"})
        files = {"csv": out_dir / f"{name}.csv", "tif": out_dir / f"{name}.tif", "json": debug_dir / f"{name}.json"}
        try:
            if not force and all(f.exists() for f in files.values()):
                result = json.loads(files["json"].read_text())
                row = {**base, "status": "skipped", **pipeline.stats(result), "files": {k: str(v) for k, v in files.items()}}
                log("    skipped: results exist")
            else:
                with item_lock or contextlib.nullcontext():  # the API shares one OCR worker between jobs
                    row = pipeline.process(pair["image"], pair["json"], out_dir, name, cache_dir, tmp_root,
                                           force=force, log=lambda m: log("    " + m), debug_dir=debug_dir)
                row.pop("result", None)
                row = {**base, **row, **header_cols(row.get("header"))}
        except Exception as e:
            row = {**base, "status": "error", "error": f"{type(e).__name__}: {e}"}
            log("    error: " + row["error"])
            log(traceback.format_exc())
        rows.append(row)
        if on_item:
            on_item(i, row)
        write_summary(rows, out_dir)  # after every log, so it's there even if the batch is stopped
    return rows
