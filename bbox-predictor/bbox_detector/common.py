"""Shared geometry, annotation, and bounded-raster image utilities.

TIFF inputs are trusted local scans. Their headers are opened directly rather
than changing Pillow's process-wide decompression-bomb setting. Raster decoding
uses libvips when available; large TIFFs require it instead of silently loading
the whole document into a Pillow RGB image.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from PIL import Image, TiffImagePlugin

CLASS_NAMES = (
    "depth_column",
    "interpreted_lithology_column",
    "lithology_legend",
)
Box = tuple[float, float, float, float]
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = (
    REPO_ROOT / "hack27-20261107T141707Z-1-001" / "hack27" / "project" / "dataset"
)
PILLOW_PIXEL_LIMIT = 100_000_000


def find_dataset(path: str | Path | None = None) -> Path:
    """Resolve the supplied dataset, or discover the bundled dataset."""
    if path is not None:
        root = Path(path).expanduser().resolve()
        if (root / "images").is_dir() and (root / "bbox_annotation").is_dir():
            return root
        raise FileNotFoundError(f"Dataset needs images/ and bbox_annotation/: {root}")
    candidates = [REPO_ROOT / "dataset", DEFAULT_DATASET, *sorted(REPO_ROOT.glob("*/hack27/project/dataset"))]
    for root in candidates:
        if (root / "images").is_dir() and (root / "bbox_annotation").is_dir():
            return root.resolve()
    raise FileNotFoundError("No bundled dataset found; supply --dataset PATH")


def document_tiles(width: int, height: int, overlap: float = 0.5) -> list[tuple[int, int, int, int]]:
    """Full-width square crops, including an exactly bottom-anchored last crop."""
    if width <= 0 or height <= 0:
        raise ValueError("Image dimensions must be positive")
    if not 0 <= overlap < 1:
        raise ValueError("overlap must be in [0, 1)")
    if height <= width:
        return [(0, 0, width, height)]
    step = max(1, round(width * (1 - overlap)))
    starts = list(range(0, height - width + 1, step))
    if starts[-1] != height - width:
        starts.append(height - width)
    return [(0, y, width, y + width) for y in starts]


def clamp_box(box: Box | list[float], width: int, height: int) -> list[int]:
    """Outward-rounded integer coordinates with exclusive right/bottom edges."""
    if len(box) != 4 or not all(math.isfinite(float(v)) for v in box):
        raise ValueError(f"Invalid box: {box!r}")
    x1, y1, x2, y2 = box
    if x2 < x1 or y2 < y1:
        raise ValueError(f"Reversed box: {box!r}")
    return [
        max(0, min(width, math.floor(x1))),
        max(0, min(height, math.floor(y1))),
        max(0, min(width, math.ceil(x2))),
        max(0, min(height, math.ceil(y2))),
    ]


def load_annotation(path: str | Path) -> dict[str, Any]:
    """Read the existing three-class JSON contract and reject malformed labels."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Annotation must be an object: {path}")
    for field in ("width", "height"):
        value = data.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"Invalid {field} in {path}")
    name = data.get("image_name")
    if not isinstance(name, str) or Path(name).name != name or not name:
        raise ValueError(f"image_name must be a filename in {path}")
    boxes = data.get("bounding_boxes")
    if not isinstance(boxes, dict) or set(boxes) != set(CLASS_NAMES):
        raise ValueError(f"Expected exactly the classes {CLASS_NAMES} in {path}")
    for cls in CLASS_NAMES:
        box = boxes[cls]
        if (
            not isinstance(box, (list, tuple))
            or len(box) != 4
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in box)
            or not all(math.isfinite(v) for v in box)
        ):
            raise ValueError(f"Invalid {cls} coordinates in {path}")
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= data["width"] and 0 <= y1 < y2 <= data["height"]):
            raise ValueError(f"{cls} outside image or empty in {path}: {box}")
    depth = boxes[CLASS_NAMES[0]]
    lithology = boxes[CLASS_NAMES[1]]
    if (depth[1], depth[3]) != (lithology[1], lithology[3]):
        raise ValueError(
            f"Column annotations must share ymin/ymax in {path}: "
            f"{CLASS_NAMES[0]}={depth}, {CLASS_NAMES[1]}={lithology}"
        )
    return data


def write_json(path: str | Path, payload: Any) -> None:
    """Write complete JSON atomically, including output directory creation."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temp_name = handle.name
            json.dump(payload, handle, indent=2, allow_nan=False, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if temp_name is not None and os.path.exists(temp_name):
            os.unlink(temp_name)


class Scan:
    """Lazy local image with a Pillow-compatible crop surface."""

    def __init__(self, path: Path):
        self.path = path
        with path.open("rb") as handle:
            signature = handle.read(4)
        self.is_tiff = signature in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")
        # Explicit TIFF construction only reads the IFD. It does not change
        # Image.MAX_IMAGE_PIXELS or the guard used for unrelated image files.
        self._pil = TiffImagePlugin.TiffImageFile(path) if self.is_tiff else Image.open(path)
        self.width, self.height = self._pil.size
        self.size = self._pil.size
        self.mode = self._pil.mode
        if self.width <= 0 or self.height <= 0 or self.width * self.height > 2_000_000_000:
            self._pil.close()
            raise ValueError(f"Unsupported scan dimensions: {self.size}")
        self._vips = None
        self._vips_checked = False
        # Useful for audit reports: a one-strip TIFF can still require a large
        # codec buffer even though crop conversion never creates full-page RGB.
        tags = getattr(self._pil, "tag_v2", {})
        self.rows_per_strip = int(tags.get(278, self.height)) if self.is_tiff else None
        self.max_decoded_strip_bytes = (
            min(self.height, self.rows_per_strip) * self.width * len(self._pil.getbands())
            if self.is_tiff else None
        )

    def _backend(self):
        if self.is_tiff and not self._vips_checked:
            self._vips_checked = True
            try:
                import pyvips
            except (ImportError, OSError):
                pass
            else:
                # Do not retain old documents (and their decompressed temporary
                # files) in libvips' global operation cache after Scan.close().
                pyvips.cache_set_max(0)
                # Several supplied TIFFs contain one compressed strip hundreds
                # of thousands of rows high. libtiff's default 50 MB allocation
                # cap rejects those valid local scans before any crop is read.
                self._vips = pyvips.Image.new_from_file(
                    str(self.path), access="random", unlimited=True, fail_on="error"
                )
        if self._vips is None and self.width * self.height > PILLOW_PIXEL_LIMIT:
            raise RuntimeError(
                f"Large TIFF {self.path.name} needs streaming libvips decoding. "
                "Install dependencies with pip install 'pyvips[binary]' before exporting or predicting."
            )
        return self._vips

    def crop(self, box: tuple[int, int, int, int] | list[int]) -> Image.Image:
        return read_crop(self, box)

    def close(self):
        self._vips = None
        self._pil.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


@contextmanager
def open_scan(path: str | Path) -> Iterator[Scan]:
    """Open one trusted local scan, keeping normal Pillow guards unchanged."""
    path = Path(path).expanduser().resolve()
    try:
        scan = Scan(path)
    except (Image.DecompressionBombError, SyntaxError) as error:
        raise OSError(f"Could not open scan {path.name}: {error}") from error
    try:
        yield scan
    finally:
        scan.close()


def _read_crop(
    scan: Scan,
    box: tuple[int, int, int, int] | list[int],
    size: tuple[int, int] | None = None,
) -> Image.Image:
    """Read a crop as RGB, resizing before allocating the final bitmap."""
    x1, y1, x2, y2 = map(int, box)
    if not (0 <= x1 < x2 <= scan.width and 0 <= y1 < y2 <= scan.height):
        raise ValueError(f"Crop {box} outside scan {scan.size}")
    if size is not None and (len(size) != 2 or min(size) <= 0):
        raise ValueError("size must contain positive width and height")
    vips = scan._backend()
    if vips is None:
        result = scan._pil.crop((x1, y1, x2, y2))
        if size is not None:
            result = result.resize(size, getattr(Image, "Resampling", Image).LANCZOS)
        return result.convert("RGB")
    crop = vips.crop(x1, y1, x2 - x1, y2 - y1)
    if size is not None:
        crop = crop.resize(size[0] / crop.width, vscale=size[1] / crop.height)
    if crop.interpretation not in ("srgb", "b-w"):
        crop = crop.colourspace("srgb")
    if crop.bands == 2 and crop.hasalpha():
        crop = crop.flatten(background=[255])
    if crop.bands == 1:
        crop = crop.bandjoin([crop, crop])
    elif crop.bands >= 4:
        crop = crop.flatten(background=[255, 255, 255]) if crop.hasalpha() else crop.extract_band(0, n=3)
    if crop.format != "uchar":
        crop = crop.cast("uchar", shift=True)
    return Image.frombytes("RGB", (crop.width, crop.height), crop.write_to_memory())


def read_crop(
    scan: Scan,
    box: tuple[int, int, int, int] | list[int],
    size: tuple[int, int] | None = None,
) -> Image.Image:
    """Read one region, translating codec errors into actionable I/O failures."""
    try:
        return _read_crop(scan, box, size=size)
    except (ValueError, RuntimeError, OSError):
        raise
    except Exception as error:
        # pyvips.Error derives directly from Exception, not OSError. Importing
        # the optional backend here also keeps CPU-only geometry tests light.
        if type(error).__module__.startswith("pyvips") or isinstance(error, Image.DecompressionBombError):
            raise OSError(f"Could not decode crop of {scan.path.name}: {error}") from error
        raise
