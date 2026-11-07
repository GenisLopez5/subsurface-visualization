"""
One log, end to end: extraction (cached) -> drawing with the notebook code -> output files.
Used by the API, by batch mode and by the command line.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import tempfile
import time
from pathlib import Path

from PIL import Image

from . import drawing
from . import extract as X

Image.MAX_IMAGE_PIXELS = None


class LogTee(io.TextIOBase):
    """File-like object that passes extract.py's progress lines (stderr) to a callback."""

    def __init__(self, emit):
        self.emit, self.buf = emit, ""

    def write(self, s):
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip() and "[INFO]" not in line and "[RapidOCR]" not in line:
                self.emit(line.rstrip())
        return len(s)


def extract_cached(img_path, ann_path, cache_dir: Path, force=False, log=print):
    """Run extract.extract, or reuse the saved result for the same image + annotation.
    Returns (result dict, cache file path)."""
    img_path, ann_path = Path(img_path), Path(ann_path)
    key = hashlib.sha1(
        ann_path.read_bytes() + str(img_path.resolve()).encode() + str(img_path.stat().st_size).encode()
    ).hexdigest()[:12]
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"{img_path.stem}.{key}.json"
    if cache.exists() and not force:
        log(f"cached: {cache.name}")
        return json.loads(cache.read_text()), cache
    log("extracting")
    with contextlib.redirect_stderr(LogTee(log)):
        result = X.extract(str(img_path), str(ann_path), verbose=True)
    cache.write_text(json.dumps(result, indent=2))
    return result, cache


def page_for_notebook(img_path, result, tmp_dir, log=print):
    """The notebook opens the page with Image.open, which can't read a PDF: render it to a temp PNG.
    Returns (page file, PIL image)."""
    img_path = Path(img_path)
    if img_path.suffix.lower() == ".pdf":
        log("rendering PDF")
        image = X.load_page(img_path, result)
        page = Path(tmp_dir) / "page.png"
        image.save(page)
        return page, image
    return img_path, Image.open(img_path)


def stats(result) -> dict:
    c = result["depth_calibration"]
    iv = result["intervals"]
    return {
        "intervals": len(iv),
        "top_depth": iv[0]["top_depth"] if iv else "",
        "bottom_depth": iv[-1]["bottom_depth"] if iv else "",
        "px_per_depth_unit": round(c["px_per_depth_unit"], 3),
        "depth_labels_used": c["n_labels_used"],
        "depth_fit_rms_px": round(c["rms_residual_px"], 2),
        "legend_entries": len(result["legend"]),
    }


def process(img_path, ann_path, out_dir: Path, name: str, cache_dir: Path, tmp_root: Path,
            force=False, log=print, debug_dir: Path | None = None, scale=None) -> dict:
    """Extract + draw one log.

    Writes out_dir/<name>.csv and out_dir/<name>.tif (LZW-compressed) and, if
    debug_dir is given, debug_dir/<name>.json. Returns a summary dict including the file paths.
    """
    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    result, _ = extract_cached(img_path, ann_path, cache_dir, force=force, log=log)
    files = {"csv": out_dir / f"{name}.csv", "tif": out_dir / f"{name}.tif"}
    drawing.write_results_csv(result["intervals"], files["csv"])
    if debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        files["json"] = debug_dir / f"{name}.json"
        files["json"].write_text(json.dumps(result, indent=2))
    tmp_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="process_", dir=tmp_root))
    try:
        page, image = page_for_notebook(img_path, result, tmp, log)
        log("drawing")
        drawing.render_tif(image, ann_path, result, files["tif"], scale=scale)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"status": "done", "seconds": round(time.time() - t0), **stats(result),
            "files": {k: str(v) for k, v in files.items()}, "result": result}
