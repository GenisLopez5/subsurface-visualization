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
from . import header_meta

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
    method = X.legend_method()  # part of the key: switching LITHO_LEGEND_METHOD never reuses old results
    key = hashlib.sha1(
        ann_path.read_bytes() + str(img_path.resolve()).encode() + str(img_path.stat().st_size).encode()
        + method.encode()
    ).hexdigest()[:12]
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"{img_path.stem}.{key}.json"
    if cache.exists() and not force:
        log(f"cached: {cache.name}")
        return json.loads(cache.read_text()), cache
    log(f"extracting (legend method: {method})")
    with contextlib.redirect_stderr(LogTee(log)):
        result = X.extract(str(img_path), str(ann_path), verbose=True, legend_method_name=method)
    cache.write_text(json.dumps(result, indent=2))
    return result, cache


def header_cached(img_path, ann_path, cache_dir: Path, force=False, log=print) -> dict | None:
    """Date + location from the log's header (header_meta, via a vision model on Ollama), or reuse
    the saved one. None when it's switched off (no OLLAMA_BASE_URL). Never raises: a failure comes
    back as {"error": ...} and isn't cached, so the next run tries again."""
    if not header_meta.enabled():
        return None
    img_path, ann_path = Path(img_path), Path(ann_path)
    model = header_meta.model_name()
    key = hashlib.sha1(
        ann_path.read_bytes() + str(img_path.resolve()).encode() + str(img_path.stat().st_size).encode()
        + model.encode() + header_meta.VERSION.encode()
    ).hexdigest()[:12]
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"{img_path.stem}.{key}.header.json"
    if cache.exists() and not force:
        log(f"cached header: {cache.name}")
        return json.loads(cache.read_text())
    log(f"reading header ({model})")
    try:
        header = header_meta.extract_header(img_path, json.loads(ann_path.read_text()), model)
    except Exception as e:  # the model server being down must not cost the lithology result
        log(f"header failed: {type(e).__name__}: {e}")
        return {"error": f"{type(e).__name__}: {e}", "model": model}
    loc = header.get("location") or {}
    log(f"header: date {header.get('date')}, lat {loc.get('lat')}, lon {loc.get('lon')}"
        + (f" ({'; '.join(header['warnings'])})" if header.get("warnings") else ""))
    cache.write_text(json.dumps(header, indent=2, ensure_ascii=False))
    return header


def satellite_view(header: dict | None, out_path: Path, log=print) -> dict | None:
    """Satellite image around the well location read from the header (satellite.py: Esri World Imagery,
    EOX Sentinel-2 offshore). Writes out_path (PNG) and returns {provider, attribution, km, lat, lon},
    or None when there is no location / it's switched off (LITHO_SATELLITE_KM=0). Optional extra:
    never raises, whatever goes wrong (no network, odd header, missing module) the log is still processed."""
    try:
        from . import satellite
        loc = (header or {}).get("location") if isinstance(header, dict) else None
        if not isinstance(loc, dict) or loc.get("lat") is None or loc.get("lon") is None:
            return None
        km = satellite.default_km()
        if not km > 0:  # also catches NaN
            return None
        lat, lon = float(loc["lat"]), float(loc["lon"])
        png, provider, km = satellite.fetch_satellite(lat, lon, km=km)  # km: zoomed out over open water
        out_path.write_bytes(png)
    except Exception as e:
        log(f"satellite view skipped: {type(e).__name__}: {e}")
        return None
    log(f"satellite view: {km} km around {lat}, {lon} ({provider})")
    return {"provider": provider, "attribution": satellite.ATTRIBUTION[provider], "km": km, "lat": lat, "lon": lon}


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
            force=False, log=print, debug_dir: Path | None = None) -> dict:
    """Extract + draw one log.

    Writes out_dir/<name>.csv and out_dir/<name>.tif (full resolution, Deflate-compressed), the two
    legends as PNGs: out_dir/<name>_legend_original.png (the log's own legend, cut from the page) and
    out_dir/<name>_legend_generated.png (the colour key drawn on top of the TIF), and, if debug_dir is
    given, debug_dir/<name>.json. With a location in the header, also out_dir/<name>_satellite_view.png
    (see satellite_view). Returns a summary dict including the file paths, the TIF's layout (legend bar
    height on top, scale: see drawing.make_layout), `header` (date + location read from the log's
    header, see header_cached; None when switched off) and `satellite` (None without an image).
    """
    t0 = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    result, _ = extract_cached(img_path, ann_path, cache_dir, force=force, log=log)
    header = header_cached(img_path, ann_path, cache_dir, force=force, log=log)
    files = {"csv": out_dir / f"{name}.csv", "tif": out_dir / f"{name}.tif"}
    sat = satellite_view(header, out_dir / f"{name}_satellite_view.png", log=log)
    if sat is not None:
        files["satellite_view"] = out_dir / f"{name}_satellite_view.png"
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
        files["legend_generated"] = out_dir / f"{name}_legend_generated.png"
        layout = drawing.render_tif(image, ann_path, result, files["tif"], key_path=files["legend_generated"])
        legend_box = drawing.crop_legend(image, ann_path, out_dir / f"{name}_legend_original.png")
        if legend_box is not None:
            files["legend_original"] = out_dir / f"{name}_legend_original.png"
            layout = {**layout, "legend_original_box": legend_box}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"status": "done", "seconds": round(time.time() - t0), **stats(result),
            "layout": layout, "header": header, "satellite": sat, "files": {k: str(v) for k, v in files.items()}, "result": result}
