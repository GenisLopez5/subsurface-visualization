"""Header metadata (dates + well location) from a mud log's header, read by a local vision LLM.

Experimental. Two steps, kept apart on purpose:
  1. Transcribe: crop the header (everything above the depth column), send it to a vision model on
     Ollama and ask for the date / location fields *exactly as printed* (JSON-schema constrained).
  2. Normalise in Python (deterministic, testable): dates -> ISO 8601, coordinates -> WGS84-style
     decimal degrees, so every log ends up with the same format whatever the header looked like.

Output (`extract_header`):
    {
      "date": "1999-05-17",                 # best single date: spud, else log start, else log/print date
      "dates": {"spud": ..., "log_from": ..., "log_to": ..., "td": ..., "log": ...},   # ISO or null
      "location": {"lat": 27.388760, "lon": -90.259495, "label": "wellhead"} | null,
      "geojson": {"type": "Point", "coordinates": [lon, lat]} | null,
      "grid": {"northing": ..., "easting": ..., "unit": "ft"} | null,     # projected, not converted
      "plss": {"section": ..., "township": ..., "range": ..., "footage": ...} | null,
      "raw": {...the model's transcription...},
      "warnings": [...], "model": ..., "seconds": ...
    }
Decimal degrees are rounded to 6 places (~0.1 m), negative for S / W. The datum is whatever the log
used (often NAD27 in the Gulf of Mexico, a few tens of metres from WGS84): good enough to look up
imagery, not for surveying.

CLI:  python -m lithology.header_meta IMAGE [IMAGE...] --annotations DIR [--model M] [--out FILE]
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

from PIL import Image

from .extract import crop_rgb, load_page

Image.MAX_IMAGE_PIXELS = None

DEFAULT_MODEL = os.environ.get("LITHO_HEADER_MODEL", "qwen3-vl:8b-instruct")
VERSION = "2"  # bump when the prompt / normalisation changes: cached headers are then recomputed


def enabled() -> bool:
    """On when an Ollama server is configured (OLLAMA_BASE_URL) and LITHO_HEADER_MODEL isn't 'off'."""
    if not os.environ.get("OLLAMA_BASE_URL"):
        from .config import _load_dotenv
        _load_dotenv()
    model = os.environ.get("LITHO_HEADER_MODEL", DEFAULT_MODEL)
    return bool(os.environ.get("OLLAMA_BASE_URL")) and model.lower() not in ("", "off", "none", "0")


def model_name() -> str:
    return os.environ.get("LITHO_HEADER_MODEL", DEFAULT_MODEL)


SUMMARY_KEYS = ("date", "dates", "location", "geojson", "all_coordinates", "grid", "plss", "warnings",
                "model", "error")


def summary(header: dict | None) -> dict | None:
    """The header without the model's raw transcription (for stats / API responses)."""
    return None if header is None else {k: header.get(k) for k in SUMMARY_KEYS if k in header}


def maps_urls(lat: float, lon: float) -> dict:
    """Google Maps links for a point (Maps URLs API: no key needed, opens the app on phones)."""
    return {"google_maps_url": f"https://www.google.com/maps/search/?api=1&query={lat},{lon}",
            "google_maps_satellite_url": f"https://www.google.com/maps/@?api=1&map_action=map"
                                         f"&center={lat},{lon}&zoom=14&basemap=satellite"}


def well_location(header: dict | None, satellite: dict | None = None) -> dict | None:
    """What the result packet says about where/when the well is: from a stored header summary
    (stats.header) and satellite info (stats.satellite). None when the header has no location and no date."""
    header = header or {}
    loc = header.get("location") or {}
    lat, lon = loc.get("lat"), loc.get("lon")
    if (lat is None or lon is None) and not header.get("date"):
        return None
    out = {"lat": lat, "lon": lon, "label": loc.get("label"), "crs": "EPSG:4326 (as printed on the log)",
           **(maps_urls(lat, lon) if lat is not None and lon is not None else
              {"google_maps_url": None, "google_maps_satellite_url": None}),
           "geojson": header.get("geojson"), "date": header.get("date"), "dates": header.get("dates"),
           "all_coordinates": header.get("all_coordinates"), "grid": header.get("grid"),
           "plss": header.get("plss"), "warnings": header.get("warnings") or []}
    if satellite:
        out["satellite_view"] = {k: satellite.get(k) for k in ("provider", "attribution", "km")}
    return out
MAX_SIDE = int(os.environ.get("LITHO_HEADER_MAX_SIDE", "2000"))  # px sent to the model (long side)

# ----------------------------------------------------------------------------------------------
# 1. Header crop
# ----------------------------------------------------------------------------------------------


def header_box(ann: dict) -> tuple[int, int, int, int]:
    """Everything above the top of the depth / lithology columns (where the log tracks start)."""
    boxes = ann.get("bounding_boxes") or {}
    tops = [b[1] for k, b in boxes.items() if k in ("depth_column", "interpreted_lithology_column") and b]
    top = min(tops) if tops else int(ann["height"] * 0.15)
    return 0, 0, int(ann["width"]), max(200, int(top))


def header_pngs(image_path: Path, ann: dict, max_side: int = MAX_SIDE, max_tiles: int = 4) -> list[bytes]:
    """The header as PNG(s) for the model. A tall, narrow header (a whole cover page above the log) is
    cut into overlapping tiles instead of being shrunk until its small print is unreadable."""
    img = load_page(Path(image_path), ann)
    crop = Image.fromarray(crop_rgb(img, header_box(ann)))
    w, h = crop.size
    tiles = [crop]
    if h > 1.6 * w:
        th = int(1.2 * w)
        step = int(th * 0.9)
        n = min(max_tiles, -(-(h - th) // step) + 1)
        if n < -(-(h - th) // step) + 1:  # too many tiles: make each one cover more
            step = -(-(h - th) // (n - 1))
            th = int(step / 0.9)
        tiles = [crop.crop((0, y, w, min(h, y + th))) for y in range(0, max(1, h - int(th * 0.1)), step)][:n]
    out = []
    for t in tiles:
        t.thumbnail((max_side, max_side), Image.LANCZOS)
        buf = io.BytesIO()
        t.save(buf, "PNG", optimize=True)
        out.append(buf.getvalue())
    return out


# ----------------------------------------------------------------------------------------------
# 2. Transcription by the vision model
# ----------------------------------------------------------------------------------------------

_S = {"type": ["string", "null"]}
SCHEMA = {
    "type": "object",
    "properties": {
        "spud_date": _S, "log_date_from": _S, "log_date_to": _S, "td_date": _S, "log_date": _S,
        "coordinates": {
            "type": "array",
            "items": {"type": "object", "properties": {
                "label": _S, "latitude": _S, "longitude": _S}, "required": ["label", "latitude", "longitude"]},
        },
        "northing": _S, "easting": _S, "grid_unit": _S, "datum_or_zone": _S,
        "section": _S, "township": _S, "range": _S, "footage_location": _S,
        "well_name": _S, "field_or_block": _S, "country": _S, "state_or_region": _S,
    },
    "required": ["spud_date", "log_date_from", "log_date_to", "td_date", "log_date", "coordinates",
                 "northing", "easting", "grid_unit", "datum_or_zone", "section", "township", "range",
                 "footage_location", "well_name", "field_or_block", "country", "state_or_region"],
}

PROMPT = """This image is the header of a mud log / formation evaluation log from an oil or gas well.
Copy the following fields EXACTLY as printed (same digits, symbols and hemisphere letters; do not convert,
round or reformat anything). Use null when a field is not printed. Never guess or invent a value.

- spud_date: the SPUD DATE.
- log_date_from / log_date_to: the DATE range of the logged interval (e.g. "DATE: FROM x TO y").
  Only calendar dates: a DEPTH range ("FROM 4058' TO 25971'", "From: 17,133 ft") is NOT a date.
- td_date: the date total depth was reached ("Date TD").
- log_date: any other single date on the header (e.g. "Date: Nov 14, 2012") when there is no range.
- coordinates: every latitude/longitude pair printed, with its label (e.g. "wellhead", "seabed",
  "surface", or "" if unlabelled). Copy each value whole, e.g. "27deg 23'19.535\\"N", "90 deg 17' 07.746\\" W".
- northing / easting: projected X/Y coordinates if printed (e.g. "NORTHING (Y): 10,247,383'"), with
  grid_unit (ft or m) and datum_or_zone if printed (e.g. "NAD27", "UTM 15N").
- section / township / range / footage_location: US land survey location if printed
  (e.g. "SEC 27", "TWP 31N", "RGE 51E", "1143' FSL & 1875' FWL").
- well_name, field_or_block: as printed (e.g. "OCS-G 26419 001", "Green Canyon Blk 562", "WR-969").
- country, state_or_region: as printed (state, county, offshore area...)."""


def ollama_url() -> str:
    url = os.environ.get("OLLAMA_BASE_URL")
    if not url:
        from .config import _load_dotenv
        _load_dotenv()
        url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    return url.rstrip("/")


def ocr_text(pngs: list[bytes]) -> str:
    return "\n".join(_ocr_one(p) for p in pngs)


def _ocr_one(png: bytes) -> str:
    """RapidOCR over one header image, as reading-order lines (a hint for the model's digits)."""
    import numpy as np
    from .extract import ocr
    arr = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))
    words = sorted(ocr(arr), key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    lines, cur, cy = [], [], None
    for x0, y0, x1, y1, t, _ in words:
        yc = (y0 + y1) / 2
        if cur and abs(yc - cy) > max(8, (y1 - y0) * 0.6):
            lines.append(" ".join(w for _, w in sorted(cur)))
            cur = []
        cur.append((x0, t))
        cy = yc
    if cur:
        lines.append(" ".join(w for _, w in sorted(cur)))
    return "\n".join(lines)


def transcribe(pngs: list[bytes], model: str = DEFAULT_MODEL, timeout: float = 600, hint: str | None = None) -> dict:
    prompt = PROMPT
    if len(pngs) > 1:
        prompt += f"\n\nThe header is given as {len(pngs)} overlapping pieces, top to bottom."
    if hint:
        prompt += ("\n\nOCR text of the same image (may contain misreads; trust the image when they disagree, "
                   "but use it to check digits):\n" + hint)
    body = {
        "model": model, "stream": False, "format": SCHEMA,
        "options": {"temperature": 0, "num_ctx": 16384},
        "messages": [{"role": "user", "content": prompt, "images": [base64.b64encode(p).decode() for p in pngs]}],
    }
    if model.startswith("qwen3"):
        body["think"] = False
    req = urllib.request.Request(f"{ollama_url()}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        content = json.loads(r.read())["message"]["content"]
    return json.loads(content)


# ----------------------------------------------------------------------------------------------
# 3. Normalisation (pure functions)
# ----------------------------------------------------------------------------------------------

_HEMI = {"N": 1, "S": -1, "E": 1, "W": -1}


def parse_angle(text: str | None, axis: str) -> float | None:
    """'27deg 23'19.535"N', '28º 13' 07.892" N', 'N 27 23 19.5', '-90.2595' -> signed decimal degrees.

    axis is 'lat' or 'lon'. Without a hemisphere letter the sign of the number is used.
    Returns None when it can't be read or is out of range.
    """
    if not text:
        return None
    t = text.upper().replace(",", ".")
    t = re.sub(r"\b(LAT|LATITUDE|LONG|LON|LONGITUDE)\b\.?:?", " ", t)
    hemi = re.findall(r"(?<![A-Z])([NSEW])(?![A-Z])", re.sub(r"DEG", " ", t))
    nums = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", t)]
    if not nums or len(nums) > 3:
        return None
    d = nums[0]
    m = nums[1] if len(nums) > 1 else 0.0
    s = nums[2] if len(nums) > 2 else 0.0
    if m >= 60 or s >= 60 or m < 0 or s < 0:
        return None
    val = abs(d) + m / 60 + s / 3600
    sign = -1 if d < 0 else 1
    if hemi:
        h = hemi[-1]
        if (axis == "lat") != (h in "NS"):
            return None  # e.g. a 'W' on a latitude: transcription mix-up
        sign = _HEMI[h]
    val *= sign
    lim = 90 if axis == "lat" else 180
    return round(val, 6) if -lim <= val <= lim else None


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _year(y: int) -> int:
    return y if y >= 100 else (1900 + y if y >= 50 else 2000 + y)


def _numeric_parts(text: str):
    m = re.fullmatch(r"\s*(\d{1,4})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{2,4})\s*", text)
    return tuple(int(g) for g in m.groups()) if m else None


def day_first_hint(texts) -> bool:
    """True if any numeric date of this header can only be day/month (e.g. 21/06/2011)."""
    for t in texts:
        p = t and _numeric_parts(t)
        if p and p[0] <= 31 and p[0] > 12 and p[1] <= 12:
            return True
    return False


def parse_date(text: str | None, day_first: bool = False) -> str | None:
    """'May 17, 1999', '28 Jan, 2008', '05/17/99', '21/06/2011', '1-13-08', '2008-01-28' -> ISO date."""
    if not text:
        return None
    t = text.strip()
    # the model sometimes keeps the label ("DATE: FROM 05/17/99"): pull out the date-looking part
    m = re.search(r"\d{1,4}\s*[/.\-]\s*\d{1,2}\s*[/.\-]\s*\d{2,4}", t)
    if m:
        t = m.group()
    try:
        p = _numeric_parts(t)
        if p:
            a, b, c = p
            if a > 31:  # yyyy-mm-dd
                return date(a, b, c).isoformat()
            mo, d = (b, a) if (day_first or a > 12) else (a, b)
            return date(_year(c), mo, d).isoformat()
        low = t.lower()
        mon = next((n for k, n in _MONTHS.items() if re.search(rf"\b{k}[a-z]*\.?", low)), None)
        nums = [int(n) for n in re.findall(r"\d+", low)]
        if mon and len(nums) >= 2:
            y = next((n for n in nums if n > 31), nums[-1])
            d = next(n for n in nums if n != y)
            return date(_year(y), mon, d).isoformat()
    except (ValueError, StopIteration):
        pass
    return None


def _num(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", text)
    return float(m.group().replace(",", "")) if m else None


_US = re.compile(r"\bUSA?\b|U\.S\.A|UNITED STATES|GULF|GOM|\bOCS\b|OCS-|LOUISIANA|TEXAS|COLORADO|WYOMING|"
                 r"NEW MEXICO|OKLAHOMA|NORTH DAKOTA|UTAH|NEVADA|MISSISSIPPI CANYON|GREEN CANYON|GARDEN BANKS|"
                 r"WALKER RIDGE|ATWATER|KEATHLEY|ALAMINOS|EWING BANK|VIOSCA KNOLL|DESOTO")


def _clean_label(label: str | None) -> str | None:
    """'Surface Coordinates' -> 'surface coordinates'; bare field names ('Lat:', 'Latitude') -> None."""
    t = re.sub(r"\b(lat\w*|long\w*|lon|coordinates?|location)\b[.:]?", "", label or "", flags=re.I)
    return (label or "").strip(" :").lower() if t.strip(" :,.-") else None


def _axis(text: str | None) -> str | None:
    """'lat' / 'lon' from the hemisphere letter in a coordinate string, None if there is none."""
    t = re.sub(r"DEG", " ", (text or "").upper())
    h = re.findall(r"(?<![A-Z])([NSEW])(?![A-Z])", t)
    return None if not h else ("lat" if h[-1] in "NS" else "lon")


def pair_coordinates(entries: list[dict]) -> list[dict]:
    """Fix the model's pairing: values are put on the axis their hemisphere letter says, and half
    entries (only a latitude, or only a longitude) are paired up in order."""
    full, lats, lons = [], [], []
    for e in entries:
        lab = e.get("label")
        vals = [v for v in (e.get("latitude"), e.get("longitude")) if v]
        axes = [_axis(v) for v in vals]
        if len(vals) == 2:
            if axes == ["lon", "lat"]:
                vals.reverse()
            full.append({"label": lab, "latitude": vals[0], "longitude": vals[1]})
        elif len(vals) == 1:
            ax = axes[0] or ("lat" if e.get("latitude") else "lon")
            (lats if ax == "lat" else lons).append((lab, vals[0]))
    for (la_lab, la), (lo_lab, lo) in zip(lats, lons):
        lab = re.sub(r"\b(lat\w*|long\w*|lon)\b[.:]?", "", f"{la_lab or ''}", flags=re.I).strip() or None
        full.append({"label": lab, "latitude": la, "longitude": lo})
    return full


def normalise(raw: dict) -> dict:
    warnings = []
    keys = {"spud": "spud_date", "log_from": "log_date_from", "log_to": "log_date_to",
            "td": "td_date", "log": "log_date"}
    df = day_first_hint(raw.get(k) for k in keys.values())
    dates = {}
    for k, rk in keys.items():
        dates[k] = parse_date(raw.get(rk), df)
        looks_like_date = re.search(r"\d+\s*[/.\-]\s*\d+\s*[/.\-]\s*\d+|[A-Za-z]{3}.*\d{2}", raw.get(rk) or "")
        if raw.get(rk) and not dates[k] and looks_like_date and not re.search(r"\bft\b|'", raw[rk]):
            warnings.append(f"could not parse {rk}: {raw[rk]!r}")
    best = next((dates[k] for k in ("spud", "log_from", "log", "td", "log_to") if dates[k]), None)

    points = []
    context = " ".join(raw.get(k) or "" for k in ("country", "state_or_region", "well_name", "field_or_block"))
    in_us = bool(_US.search(context.upper())) or bool(raw.get("township"))
    for c in pair_coordinates(raw.get("coordinates") or []):
        lat, lon = parse_angle(c.get("latitude"), "lat"), parse_angle(c.get("longitude"), "lon")
        if lat is None or lon is None:
            text = f"{c.get('latitude') or ''} {c.get('longitude') or ''}"
            if re.search(r"\d+\s*(?:°|º|DEG|')", text.upper()) and not re.search(r"F[NSEW]L|SEC", text.upper()):
                warnings.append(f"could not parse coordinates {c}")
            continue
        if lon > 0 and in_us:  # no hemisphere, or an 'E' printed on a US well (seen on a real header)
            lon = -lon
            warnings.append(f"longitude {c.get('longitude')!r} is east/unsigned on a US well: used W")
        points.append({"lat": lat, "lon": lon, "label": _clean_label(c.get("label"))})
    if len(points) > 1:
        for p in points[1:]:
            dist = max(abs(p["lat"] - points[0]["lat"]), abs(p["lon"] - points[0]["lon"]))
            if dist > 0.05:  # > ~5 km apart: one of them is probably a typo on the log or a misread
                warnings.append(f"coordinate pairs disagree by {dist:.3f} deg: {points}")
                break
    # prefer the surface / wellhead location: that's what you'd look up imagery for
    order = ("wellhead", "surface", "")
    loc = next((p for o in order for p in points if o in (p["label"] or "")), points[0] if points else None)

    grid = None
    if raw.get("northing") and raw.get("easting"):
        grid = {"northing": _num(raw["northing"]), "easting": _num(raw["easting"]),
                "unit": raw.get("grid_unit"), "datum_or_zone": raw.get("datum_or_zone")}
        if not loc:
            warnings.append("only projected northing/easting: no lat/lon (needs the datum/zone to convert)")
    plss = None
    if raw.get("section") or raw.get("township") or raw.get("footage_location"):
        plss = {"section": raw.get("section"), "township": raw.get("township"), "range": raw.get("range"),
                "footage": raw.get("footage_location"), "state": raw.get("state_or_region")}
        if not loc:
            warnings.append("only a US land-survey (PLSS) location: no lat/lon")
    return {
        "date": best, "dates": dates,
        "location": loc,
        "geojson": {"type": "Point", "coordinates": [loc["lon"], loc["lat"]]} if loc else None,
        "all_coordinates": points, "grid": grid, "plss": plss, "warnings": warnings,
    }


def extract_header(image_path, ann: dict, model: str = DEFAULT_MODEL, ocr_hint: bool = False) -> dict:
    t0 = time.time()
    pngs = header_pngs(Path(image_path), ann)
    raw = transcribe(pngs, model, hint=ocr_text(pngs) if ocr_hint else None)
    return {**normalise(raw), "raw": raw, "model": model + ("+ocr" if ocr_hint else ""),
            "seconds": round(time.time() - t0, 1)}


# ----------------------------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------------------------


def _find_annotation(image: Path, dirs) -> dict:
    for d in dirs:
        p = Path(d) / f"{image.stem}.json"
        if p.exists():
            return json.loads(p.read_text())
    raise FileNotFoundError(f"no annotation JSON for {image.name}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("images", nargs="+", type=Path)
    ap.add_argument("--annotations", nargs="+", required=True, help="folders with the bounding-box JSONs")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out", type=Path, help="write {image name: result} JSON here")
    ap.add_argument("--ocr-hint", action="store_true", help="also give the model RapidOCR's text of the header")
    ap.add_argument("--save-crops", type=Path, help="folder to save the header crops sent to the model")
    a = ap.parse_args(argv)
    results = {}
    for img in a.images:
        try:
            ann = _find_annotation(img, a.annotations)
            if a.save_crops:
                a.save_crops.mkdir(parents=True, exist_ok=True)
                for i, png in enumerate(header_pngs(img, ann)):
                    (a.save_crops / f"{img.stem}_{i}.png").write_bytes(png)
            r = extract_header(img, ann, a.model, a.ocr_hint)
        except Exception as e:  # keep going over a batch
            r = {"error": f"{type(e).__name__}: {e}"}
        results[img.name] = r
        loc = r.get("location") or {}
        print(f"{img.name[:60]:60s} date={r.get('date')} lat={loc.get('lat')} lon={loc.get('lon')} "
              f"{r.get('seconds', '')}s {'; '.join(r.get('warnings', [])) or r.get('error', '')}", flush=True)
        if a.out:
            a.out.write_text(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())
