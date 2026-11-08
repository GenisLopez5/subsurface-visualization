"""Satellite image around a point (e.g. the well location from `header_meta.extract_header`).

No API key, one HTTP request, stdlib + Pillow only. Two providers:
  esri  Esri World Imagery (default) - sub-metre to ~15 m depending on area, the "Google satellite" look.
        Free to use with attribution: "Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community".
  eox   EOX Sentinel-2 cloudless 2023 - 10 m, cloud-free mosaic, very clean colours, CC BY-NC-SA 4.0.
        Attribution: "Sentinel-2 cloudless - https://s2maps.eu by EOX IT Services GmbH (Contains modified
        Copernicus Sentinel data 2023)".

The box is square on the ground: `km` is its full width, centred on (lat, lon). The request is made in
Web Mercator (EPSG:3857) so the image isn't stretched, and the mercator extent is scaled by 1/cos(lat).

Python:  png, provider, km_shown = fetch_satellite(27.38876, -90.259495, km=5)   # credit: ATTRIBUTION[provider]
CLI:     python -m lithology.satellite LAT LON [--km 5] [--max-km 400] [--size 1024] [--provider esri|eox]
                                          [--no-marker] [--no-enhance] -o out.png
"""

from __future__ import annotations

import argparse
import io
import math
import sys
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageStat

def default_km() -> float:
    """Width used by the pipeline: LITHO_SATELLITE_KM (default 5; 0 switches the satellite view off)."""
    import os
    try:
        return float(os.environ.get("LITHO_SATELLITE_KM", "5"))
    except ValueError:
        return 5.0


def default_max_km() -> float:
    """How far a featureless (open water) view may zoom out: LITHO_SATELLITE_MAX_KM (default 400)."""
    import os
    try:
        return float(os.environ.get("LITHO_SATELLITE_MAX_KM", "400"))
    except ValueError:
        return 400.0


R = 6378137.0  # Web Mercator sphere radius (m)
UA = "lithology-satellite/0.1"
ATTRIBUTION = {
    "esri": "Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community",
    "eox": "Sentinel-2 cloudless - https://s2maps.eu by EOX IT Services GmbH "
           "(Contains modified Copernicus Sentinel data 2023)",
}


def _mercator(lat: float, lon: float) -> tuple[float, float]:
    lat = max(min(lat, 85.0511), -85.0511)
    return R * math.radians(lon), R * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


def bbox_3857(lat: float, lon: float, km: float) -> tuple[float, float, float, float]:
    """(xmin, ymin, xmax, ymax) in EPSG:3857 for a `km`-wide square on the ground centred on the point."""
    x, y = _mercator(lat, lon)
    half = km * 1000 / 2 / math.cos(math.radians(lat))
    return x - half, y - half, x + half, y + half


def _url(provider: str, bbox: tuple[float, float, float, float], size: int) -> str:
    b = ",".join(f"{v:.2f}" for v in bbox)
    if provider == "esri":
        q = {"bbox": b, "bboxSR": 3857, "imageSR": 3857, "size": f"{size},{size}",
             "format": "jpg", "f": "image"}
        return ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export?"
                + urllib.parse.urlencode(q))
    if provider == "eox":
        q = {"service": "WMS", "request": "GetMap", "version": "1.1.1", "layers": "s2cloudless-2023_3857",
             "styles": "", "srs": "EPSG:3857", "bbox": b, "width": size, "height": size, "format": "image/jpeg"}
        return "https://tiles.maps.eox.at/wms?" + urllib.parse.urlencode(q)
    raise ValueError(f"unknown provider {provider!r} (esri|eox)")


def _get(provider: str, lat: float, lon: float, km: float, size: int, timeout: float) -> Image.Image:
    req = urllib.request.Request(_url(provider, bbox_3857(lat, lon, km), size), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data, ctype = r.read(), r.headers.get("Content-Type", "")
    if not ctype.startswith("image/"):
        raise RuntimeError(f"{provider}: expected an image, got {ctype}: {data[:300]!r}")
    return Image.open(io.BytesIO(data)).convert("RGB")


def _featureless(im: Image.Image) -> bool:
    """Open water (or anything dark and flat): nothing to see, worth zooming out."""
    g = ImageStat.Stat(im.convert("L").resize((128, 128)))
    return g.mean[0] < 60 and g.stddev[0] < 20


def _enhance(im: Image.Image) -> Image.Image:
    """Mild colour/contrast lift; dark scenes (sea) are brightened towards a mid tone, at most x1.8."""
    mean = ImageStat.Stat(im.convert("L")).mean[0]
    im = ImageEnhance.Color(im).enhance(1.2)
    im = ImageEnhance.Contrast(im).enhance(1.1)
    if mean < 70:
        im = ImageEnhance.Brightness(im).enhance(min(1.8, 75 / max(mean, 1)))
    return im


def _nice_km(target: float) -> float:
    """A round scale-bar length (1, 2, 5 x 10^n km) not longer than target."""
    e = 10 ** math.floor(math.log10(target))
    return max(m * e for m in (1, 2, 5) if m * e <= target)


def _scale_bar(im: Image.Image, km: float) -> None:
    d = ImageDraw.Draw(im)
    W = im.width
    bar_km = _nice_km(km / 4)
    bar = bar_km / km * W
    pad, h = W // 40, max(4, W // 150)
    x0, y0 = pad, im.height - pad - h
    label = f"{bar_km:g} km" + (f"   (view {km:g} km wide)" if km >= 20 else "")
    try:
        font = ImageFont.load_default(size=max(12, W // 50))
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    tb = d.textbbox((0, 0), label, font=font)
    box_w = max(bar, tb[2]) + pad
    d.rectangle([x0 - pad / 2, y0 - (tb[3] - tb[1]) - pad, x0 + box_w - pad / 2, y0 + h + pad / 2],
                fill=(0, 0, 0, 140))
    d.text((x0, y0 - (tb[3] - tb[1]) - pad / 2), label, fill=(255, 255, 255), font=font)
    d.rectangle([x0, y0, x0 + bar, y0 + h], fill=(255, 255, 255))


def _fetch_one(provider: str, lat: float, lon: float, km: float, size: int, timeout: float):
    try:
        return _get(provider, lat, lon, km, size, timeout), provider
    except Exception:
        # Esri has no fine-zoom imagery over open sea (HTTP 500 offshore at a few km): EOX covers it
        if provider != "esri":
            raise
        return _get("eox", lat, lon, km, size, timeout), "eox"


def fetch_satellite(lat: float, lon: float, km: float = 5.0, size: int = 1024, provider: str = "esri",
                    marker: bool = True, timeout: float = 30.0, max_km: float | None = None,
                    enhance: bool = True) -> tuple[bytes, str, float]:
    """PNG bytes of a `size`x`size` satellite image centred on (lat, lon), `km` wide.
    Returns (png, provider used, km actually shown).

    Open water is just a dark flat square up close, so if the view is featureless it zooms out (x4 per step)
    up to `max_km` (default LITHO_SATELLITE_MAX_KM, 400) until there is something to see (coast, seafloor
    relief). Onshore wells keep `km`. With `enhance`, a mild colour/brightness lift; with `marker`, a red
    ring on the point and a scale bar. If Esri fails (e.g. offshore up close) it falls back to EOX."""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"bad coordinates {lat}, {lon}")
    size = max(64, min(int(size), 4096))
    max_km = max(km, max_km if max_km is not None else default_max_km())
    im, used = _fetch_one(provider, lat, lon, km, size, timeout)
    while _featureless(im) and km < max_km:
        km = min(km * 4, max_km)
        try:
            im, used = _fetch_one(provider, lat, lon, km, size, timeout)
        except Exception:
            break  # keep the closer view we already have
    if enhance:
        im = _enhance(im)
    if marker:
        d = ImageDraw.Draw(im)
        c, rad, w = im.width / 2, max(6, im.width // 60), max(2, im.width // 300)
        d.ellipse([c - rad, c - rad, c + rad, c + rad], outline=(255, 255, 255), width=w + 2)
        d.ellipse([c - rad, c - rad, c + rad, c + rad], outline=(230, 30, 30), width=w)
        _scale_bar(im, km)
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue(), used, km


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("lat", type=float)
    p.add_argument("lon", type=float)
    p.add_argument("--km", type=float, default=5.0, help="width of the square on the ground (default 5)")
    p.add_argument("--size", type=int, default=1024, help="image side in px (default 1024)")
    p.add_argument("--provider", choices=["esri", "eox"], default="esri")
    p.add_argument("--no-marker", action="store_true", help="no red ring / scale bar")
    p.add_argument("--no-enhance", action="store_true", help="raw colours")
    p.add_argument("--max-km", type=float, default=None, help="zoom-out limit over open water (default 400)")
    p.add_argument("-o", "--out", default="satellite.png")
    a = p.parse_args(argv)
    png, used, km = fetch_satellite(a.lat, a.lon, a.km, a.size, a.provider, marker=not a.no_marker,
                                    max_km=a.max_km, enhance=not a.no_enhance)
    with open(a.out, "wb") as f:
        f.write(png)
    print(f"{a.out}  ({km:g} km, {used})  {ATTRIBUTION[used]}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
