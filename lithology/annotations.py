"""
Getting the bounding boxes (depth column, lithology column, legend) for an image.

A JSON sent along with the image always wins. Otherwise it depends on the box source:
  dataset   (default) the existing annotation JSON matched by file name (`AnnotationStore`); the
            detector is not called.
  detector  automatic detection first (`BoxDetector`: the bbox service at LITHO_BBOX_URL, or the int8
            models in-process), the dataset JSON as fallback when it fails.
The server default is LITHO_BOX_SOURCE; every endpoint can override it per request (`boxes=`).
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


BOX_SOURCES = ("dataset", "detector")
DEFAULT_BOX_SOURCE = "detector"  # the int8 OpenVINO detector of bbox-final first, the dataset JSON as fallback


class AnnotationNotFound(LookupError):
    pass


def box_source(value: str | None, default: str = DEFAULT_BOX_SOURCE) -> str:
    """Validate a `boxes=` value ('dataset' / 'detector'); None/'' gives the default."""
    v = (value or default).strip().lower()
    if v not in BOX_SOURCES:
        raise ValueError(f"boxes must be one of {', '.join(BOX_SOURCES)}, not {value!r}")
    return v


def _plan(source: str, detector) -> tuple[bool, str]:
    """(call the detector?, reason when it isn't / the dataset is the fallback)."""
    if source != "detector":
        return False, "dataset JSON requested (boxes=dataset)"
    if detector is None:
        return False, "detection requested but no detector is configured (set LITHO_BBOX_URL)"
    return True, ""


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


def bbox_log(image_path, message: str):
    """Backend log line saying where an image's bounding boxes came from."""
    print(f"[bbox] {Path(image_path).name}: {message}", flush=True)


def _detect(image_path: Path, detector: BoxDetector) -> tuple[dict | None, str]:
    """(annotation, "") from the detector, or (None, failure reason). Logs either way."""
    try:
        ann = validate_annotation(detector.detect(image_path))
        bbox_log(image_path, "bounding boxes detected automatically")
        return ann, ""
    except Exception as e:
        failure = f"detector failed: {type(e).__name__}: {e}"
        bbox_log(image_path, failure)
        return None, failure


def _find_in_store(image_path: Path, store: AnnotationStore, why: str) -> tuple[Path, str]:
    """Existing annotation JSON for the image, logging that the dataset JSON is used (and why)."""
    size = None
    if image_path.suffix.lower() != ".pdf":
        from PIL import Image

        with Image.open(image_path) as im:
            size = im.size
    try:
        path, how = store.find(image_path.name, size)
    except AnnotationNotFound:
        bbox_log(image_path, f"no existing JSON found either ({why}); no bounding boxes")
        raise
    bbox_log(image_path, f"USING EXISTING JSON {path} ({why})" + (f"; {how}" if how else ""))
    return path, how


def resolve_annotation(image_path, store: AnnotationStore, detector: BoxDetector | None = None,
                       out_dir: Path | None = None, source: str = DEFAULT_BOX_SOURCE) -> tuple[Path, str]:
    """Annotation JSON for an image. source='dataset': the existing JSON from the store only.
    source='detector': the detector first, the store's JSON when it fails. A detected annotation is
    written to out_dir as a file."""
    image_path = Path(image_path)
    use_detector, failure = _plan(source, detector)
    if use_detector:
        ann, failure = _detect(image_path, detector)
        if ann is not None:
            out = Path(out_dir or image_path.parent) / f"{image_path.stem}.detected.json"
            out.write_text(json.dumps(ann, indent=2))
            return out, "bounding boxes detected automatically"
    try:
        path, how = _find_in_store(image_path, store, failure)
        return path, "; ".join(x for x in (how, f"existing JSON used: {failure}") if x)
    except AnnotationNotFound as e:
        hint = "; send the JSON, or use boxes=detector to detect them" if source != "detector" else ""
        raise AnnotationNotFound(f"{e} ({failure}{hint})") from None


# ---------------------------------------------------------------------------------------------
# Annotation for an uploaded image (the /api/v1 file API). Sources, first match wins:
#   uploaded  a JSON file sent in the same upload, paired by its image_name or by file name
#   provided  annotation JSON sent as data (e.g. by an external box-extraction algorithm)
#   detector  the configured BoxDetector, only with source='detector'
#   dataset   an existing JSON from the annotation folders (AnnotationStore): the default source, and the
#             fallback when the detector fails

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
                              detector: BoxDetector | None = None,
                              source: str = DEFAULT_BOX_SOURCE) -> tuple[dict | None, str | None, str]:
    """(annotation, source, note) for one uploaded image; (None, None, reason) when nothing fits.
    `uploaded`: (json file name, annotation) pairs from the same upload. `provided`: an annotation
    already chosen for this image from the request data. `source`: 'dataset' or 'detector' (see top)."""
    for json_name, ann in uploaded:
        if annotation_matches(ann, image_name, json_name):
            bbox_log(image_name, f"using the JSON uploaded with it ({json_name})")
            return ann, "uploaded", json_name
    if provided is not None:
        bbox_log(image_name, "using the bounding boxes sent with the request")
        return validate_annotation(provided), "provided", ""
    use_detector, failure = _plan(source, detector)
    if use_detector:
        ann, failure = _detect(Path(image_path), detector)
        if ann is not None:
            return ann, "detector", ""
    if store is not None:
        try:
            path, how = _find_in_store(Path(image_path), store, failure)
            ann = load_annotation(path)
            return ann, "dataset", "; ".join(x for x in (path.name + (f" ({how})" if how else ""), failure) if x)
        except AnnotationNotFound:
            pass
    else:
        bbox_log(image_name, f"no bounding boxes ({failure}; no annotation folders configured)")
    hint = "; set the boxes, or run the detector (POST .../detect-boxes)" if source != "detector" else ""
    return None, None, f"{failure}; no annotation JSON uploaded, provided or found in the dataset{hint}"
