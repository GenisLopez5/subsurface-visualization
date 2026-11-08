"""
Getting the bounding boxes (depth column, lithology column, legend) for an image.

Automatic detection (`BoxDetector`, by default the int8 models in bbox_detector.py) comes first;
existing annotation JSONs matched by file name (`AnnotationStore`) are the fallback. A JSON sent
along with the image always wins.
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


def validate_annotation(ann) -> dict:
    """Check an annotation dict: bounding_boxes with the three required boxes, each [x0, y0, x1, y1]."""
    boxes = ann.get("bounding_boxes") if isinstance(ann, dict) else None
    if not isinstance(boxes, dict) or any(k not in boxes for k in REQUIRED_BOXES):
        raise ValueError(f"not an annotation (needs bounding_boxes with {', '.join(REQUIRED_BOXES)})")
    for k in REQUIRED_BOXES:
        b = boxes[k]
        if not (isinstance(b, (list, tuple)) and len(b) == 4 and all(isinstance(v, (int, float)) for v in b)
                and b[0] < b[2] and b[1] < b[3]):
            raise ValueError(f"bounding box {k} must be [x0, y0, x1, y1] with x0 < x1 and y0 < y1")
    return ann


def load_annotation(path) -> dict:
    return validate_annotation(json.loads(Path(path).read_text()))


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
    """Annotation JSON for an image: the detector first (if any), then an existing JSON from the store
    (also used when the detector fails). A detected annotation is written to out_dir as a file."""
    image_path = Path(image_path)
    failure = ""
    if detector is not None:
        try:
            ann = validate_annotation(detector.detect(image_path))
            out = Path(out_dir or image_path.parent) / f"{image_path.stem}.detected.json"
            out.write_text(json.dumps(ann, indent=2))
            return out, "bounding boxes detected automatically"
        except Exception as e:
            failure = f"detector failed: {type(e).__name__}: {e}"
    size = None
    if image_path.suffix.lower() != ".pdf":
        from PIL import Image

        with Image.open(image_path) as im:
            size = im.size
    try:
        path, how = store.find(image_path.name, size)
        return path, "; ".join(x for x in (how, failure) if x)
    except AnnotationNotFound as e:
        raise AnnotationNotFound(f"{e}" + (f" ({failure})" if failure else "")) from None


# ---------------------------------------------------------------------------------------------
# Annotation for an uploaded image (the /api/v1 file API). Sources, first match wins:
#   uploaded  a JSON file sent in the same upload, paired by its image_name or by file name
#   provided  annotation JSON sent as data (e.g. by an external box-extraction algorithm)
#   detector  the configured BoxDetector (LITHO_DETECTOR="module:object")
#   dataset   an existing JSON from the annotation folders (AnnotationStore), if the detector fails

def annotation_matches(ann: dict, image_name: str, json_name: str = "") -> bool:
    return bool(_name_keys(Path(image_name).name) & _name_keys(ann.get("image_name", ""), json_name))


def load_detector(spec: str | None) -> BoxDetector | None:
    """'package.module:object'. The object may be a detector instance, a class (instantiated with no
    arguments) or a plain function image_path -> annotation dict."""
    if not spec:
        return None
    import importlib
    import inspect

    mod, _, attr = spec.partition(":")
    obj = getattr(importlib.import_module(mod), attr or "detector")
    if inspect.isclass(obj):
        obj = obj()
    if not hasattr(obj, "detect") and callable(obj):
        fn = obj

        class _Fn:
            def detect(self, image_path):
                return fn(image_path)
        obj = _Fn()
    return obj


def resolve_upload_annotation(image_name: str, image_path: Path, uploaded: list[tuple[str, dict]],
                              provided: dict | None, store: AnnotationStore | None,
                              detector: BoxDetector | None = None) -> tuple[dict | None, str | None, str]:
    """(annotation, source, note) for one uploaded image; (None, None, reason) when nothing fits.
    `uploaded`: (json file name, annotation) pairs from the same upload. `provided`: an annotation
    already chosen for this image from the request data."""
    for json_name, ann in uploaded:
        if annotation_matches(ann, image_name, json_name):
            return ann, "uploaded", json_name
    if provided is not None:
        return validate_annotation(provided), "provided", ""
    failure = ""
    if detector is not None:
        try:
            return validate_annotation(detector.detect(Path(image_path))), "detector", ""
        except Exception as e:  # a failing detector falls back to the dataset JSONs
            failure = f"detector failed: {type(e).__name__}: {e}"
    if store is not None:
        try:
            path, how = resolve_annotation(image_path, store)
            ann = load_annotation(path)
            return ann, "dataset", "; ".join(x for x in (path.name + (f" ({how})" if how else ""), failure) if x)
        except AnnotationNotFound:
            pass
    return None, None, failure or "no annotation JSON uploaded, provided or found in the dataset"
