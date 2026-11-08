"""
Automatic bounding boxes: find the depth column, the lithology column and the legend on a scan with Pau's and Genís's
detector (bbox-final/: two YOLO11s models exported to OpenVINO int8), so a log needs no annotation JSON.

The detector needs PyTorch + ultralytics + OpenVINO + libvips. They live in their own environment, bbox-final/.venv
(./setup_auto_boxes.sh creates it), so they never clash with this app's packages; the detector runs as a separate
process (bbox-final/predict.py) and returns the same JSON format as the organisers' annotations.

PDFs are rendered to a PNG first: at the uploaded JSON's size when there is one (so pixel rows match its grid and the
ground truth), else at 200 DPI (as lithology/bbox_detector.py does).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BBOX_DIR = Path(os.environ.get("LITHO_BBOX_DIR") or REPO / "bbox-final")
BBOX_PYTHON = Path(os.environ.get("LITHO_BBOX_PYTHON") or BBOX_DIR / ".venv" / "bin" / "python")
TIMEOUT_S = int(os.environ.get("LITHO_BBOX_TIMEOUT", "1800"))
BOXES = ("depth_column", "interpreted_lithology_column", "lithology_legend")


def available() -> tuple:
    """(ok, message): is the detector installed?"""
    if not (BBOX_DIR / "predict.py").is_file():
        return False, f"bbox-final not found at {BBOX_DIR}"
    if not BBOX_PYTHON.is_file():
        return False, "detector not installed: run ./setup_auto_boxes.sh once (creates bbox-final/.venv)"
    return True, f"bbox-final int8 models ({BBOX_PYTHON})"


def _render_pdf(pdf: Path, out_dir: Path, size: tuple | None) -> tuple:
    import pypdfium2 as pdfium
    page = pdfium.PdfDocument(str(pdf))[0]
    w_pt, h_pt = page.get_size()
    scale = size[0] / w_pt if size else min(200 / 72, (80_000_000 / (w_pt * h_pt)) ** 0.5)
    img = page.render(scale=scale).to_pil().convert("RGB")
    if size and img.size != tuple(size):
        img = img.resize(tuple(size))
    out = out_dir / f"{pdf.stem}.png"
    img.save(out)
    return out, img.size


def detect(image_path, out_path, json_hint: Path | None = None) -> dict:
    """Run the detector on one scan; write the annotation JSON to out_path and return it. Raises RuntimeError with a
    readable reason when the detector is missing, fails, or misses a required box."""
    ok, msg = available()
    if not ok:
        raise RuntimeError(msg)
    image_path = Path(image_path)
    with tempfile.TemporaryDirectory() as tmp:
        src, size = image_path, None
        if image_path.suffix.lower() == ".pdf":
            hint = None
            if json_hint and Path(json_hint).is_file():
                try:
                    a = json.loads(Path(json_hint).read_text())
                    hint = (int(a["width"]), int(a["height"]))
                except (OSError, ValueError, KeyError, TypeError):
                    hint = None
            src, size = _render_pdf(image_path, Path(tmp), hint)
        raw = Path(tmp) / "boxes.json"
        command = [str(BBOX_PYTHON), str(BBOX_DIR / "predict.py"), str(src), "--output", str(raw), "--device", "cpu"]
        diagnostics_dir = os.environ.get("LITHO_BBOX_DIAGNOSTICS_DIR")
        if diagnostics_dir:
            diagnostics = Path(diagnostics_dir) / f"{image_path.stem}.json"
            diagnostics.parent.mkdir(parents=True, exist_ok=True)
            command.extend(["--diagnostics", str(diagnostics)])
        try:
            p = subprocess.run(command, cwd=str(BBOX_DIR), capture_output=True, text=True,
                               timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"detector took longer than {TIMEOUT_S} s") from None
        if p.returncode != 0 or not raw.is_file():
            err = (p.stderr.strip().splitlines() or [f"exit code {p.returncode}"])[-1]
            raise RuntimeError(f"detector failed: {err[:300]}")
        ann = json.loads(raw.read_text())
    boxes = ann.get("bounding_boxes") or {}
    missing = [k for k in ("interpreted_lithology_column", "lithology_legend") if not _valid(boxes.get(k))]
    if missing:
        raise RuntimeError(f"detector found no {' and no '.join(missing)}")
    boxes = {k: [int(round(v)) for v in boxes[k]] for k in BOXES if _valid(boxes.get(k))}
    ann = dict(image_name=image_path.name, width=int(ann.get("width") or size[0]), height=int(ann.get("height") or size[1]),
               bounding_boxes=boxes, source="automatic bounding boxes (bbox-final int8)")
    Path(out_path).write_text(json.dumps(ann, indent=2))
    return ann


def _valid(b) -> bool:
    return isinstance(b, (list, tuple)) and len(b) == 4 and all(isinstance(v, (int, float)) for v in b) \
        and b[0] < b[2] and b[1] < b[3]


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0
