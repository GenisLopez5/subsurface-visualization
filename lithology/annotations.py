"""
Getting the bounding boxes (depth column, lithology column, legend) for an image.

Today they come from existing annotation JSONs, matched by file name (`AnnotationStore`).
Automatic detection will plug in as a `BoxDetector`: `resolve_annotation` tries the store
first and falls back to the detector, so callers (API, batch) don't change.
"""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Protocol

PAGE_EXT = {".tif", ".tiff", ".pdf"}
IMAGE_EXT = PAGE_EXT | {".png", ".jpg", ".jpeg"}
REQUIRED_BOXES = ("depth_column", "interpreted_lithology_column", "lithology_legend")


class AnnotationNotFound(LookupError):
    pass


def norm(s: str) -> str:
    """File-name key: lower-case alphanumerics only, browser '(1)' suffix removed."""
    s = re.sub(r"\(\d+\)$", "", s.strip())
    return re.sub(r"[^a-z0-9]", "", s.lower())


def load_annotation(path) -> dict:
    ann = json.loads(Path(path).read_text())
    boxes = ann.get("bounding_boxes") if isinstance(ann, dict) else None
    if not isinstance(boxes, dict) or any(k not in boxes for k in REQUIRED_BOXES):
        raise ValueError(f"not an annotation file (needs bounding_boxes with {', '.join(REQUIRED_BOXES)})")
    return ann


def _name_keys(*names: str) -> set[str]:
    keys = set()
    for n in names:
        if n:
            keys |= {norm(n), norm(Path(n).stem), norm(Path(Path(n).stem).stem)}
    return keys - {""}


class AnnotationStore:
    """Annotation JSONs in one or more folders, looked up by the image's file name."""

    def __init__(self, dirs):
        self.dirs = [Path(d) for d in dirs]

    def all(self) -> list[tuple[Path, dict]]:
        out = []
        for d in self.dirs:
            if not d.is_dir():
                continue
            for p in sorted(d.glob("*.json")):
                try:
                    out.append((p, load_annotation(p)))
                except Exception:
                    continue
        return out

    def find(self, filename: str, size: tuple[int, int] | None = None) -> tuple[Path, str]:
        """Return (json_path, note). `size` (width, height), when known, must match the JSON."""
        wanted = _name_keys(Path(filename).name)
        best = None
        for p, ann in self.all():
            if size and (ann.get("width"), ann.get("height")) != tuple(size):
                continue
            keys = _name_keys(p.name, ann.get("image_name", ""))
            if wanted & keys:
                return p, ""
            score = max(difflib.SequenceMatcher(None, w, k).ratio() for w in wanted for k in keys)
            if best is None or score > best[0]:
                best = (score, p)
        if best and best[0] >= 0.85:
            return best[1], "name is not an exact match"
        raise AnnotationNotFound(f"No JSON found for {Path(filename).name}")


class ImageStore:
    """Images in one or more folders, looked up from an annotation's image_name."""

    def __init__(self, dirs):
        self.dirs = [Path(d) for d in dirs]

    def find(self, ann: dict) -> Path | None:
        name = ann.get("image_name", "")
        for d in self.dirs:
            if (d / name).is_file():
                return d / name
        keys = _name_keys(name)
        for d in self.dirs:
            if not d.is_dir():
                continue
            for p in d.iterdir():
                if p.suffix.lower() in IMAGE_EXT and _name_keys(p.name) & keys:
                    return p
        return None


class BoxDetector(Protocol):
    """Future automatic bounding-box extraction: image file -> annotation dict
    (same format as the JSONs: width, height, bounding_boxes{...})."""

    def detect(self, image_path: Path) -> dict: ...


def resolve_annotation(image_path, store: AnnotationStore, detector: BoxDetector | None = None,
                       out_dir: Path | None = None) -> tuple[Path, str]:
    """Annotation JSON for an image: existing JSON first, then the detector (if any).
    A detected annotation is written to out_dir so the rest of the pipeline can use a file."""
    image_path = Path(image_path)
    size = None
    if image_path.suffix.lower() != ".pdf":
        from PIL import Image

        with Image.open(image_path) as im:
            size = im.size
    try:
        return store.find(image_path.name, size)
    except AnnotationNotFound:
        if detector is None:
            raise
    ann = detector.detect(image_path)
    out = Path(out_dir or image_path.parent) / f"{image_path.stem}.detected.json"
    out.write_text(json.dumps(ann, indent=2))
    return out, "bounding boxes detected automatically"
