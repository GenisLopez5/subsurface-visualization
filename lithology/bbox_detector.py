"""
Automatic bounding boxes (depth column, lithology column, legend) with the bbox-final workflow
(two YOLO11s detectors exported to OpenVINO int8).

The models run on another machine: bbox-final/server.py (start it there with bbox-final/run_server.sh)
and this app calls it over HTTP. Preprocessing stays here: PDFs are rendered to a PNG before sending,
TIF/PNG/JPG are sent as they are, so the returned boxes are in original-image pixels.

Plugs into the app as a `BoxDetector` (annotations.py) via LITHO_DETECTOR=lithology.bbox_detector:detector
(the default). Which detector that is:
    LITHO_BBOX_URL=http://<host>:8100   remote service (recommended)
    LITHO_BBOX_LOCAL=1                  run the models in this process (needs torch/ultralytics/openvino;
                                        LITHO_BBOX_DIR points to bbox-final, default: next to this package)
    neither                             no automatic detection (uploaded / dataset JSONs only)
LITHO_BBOX_TIMEOUT: seconds to wait for the service per image (default 300).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import uuid
from contextlib import redirect_stdout
from pathlib import Path

BBOX_DIR = Path(os.environ.get("LITHO_BBOX_DIR") or Path(__file__).resolve().parent.parent / "bbox-final")
PDF_DPI = 200  # PDFs are rendered at this resolution for detection; the JSON's width/height record it
PDF_MAX_PIXELS = 80_000_000


def _render_pdf(pdf: Path, out_dir: Path) -> Path:
    """First page as a PNG at PDF_DPI. extract.py later renders the page at the JSON's width, i.e. the same grid."""
    import pypdfium2 as pdfium

    page = pdfium.PdfDocument(str(pdf))[0]
    w_pt, h_pt = page.get_size()
    # PDF_DPI, but never more than PDF_MAX_PIXELS (some PDFs are scans placed at 1 pt per pixel)
    scale = min(PDF_DPI / 72, (PDF_MAX_PIXELS / (w_pt * h_pt)) ** 0.5)
    out = out_dir / f"{pdf.stem}.png"
    page.render(scale=scale).to_pil().convert("RGB").save(out)
    return out


def _prepared(image_path: Path, work: Path) -> Path:
    """The file to run detection on: the image itself, or a rendered PNG for a PDF."""
    return _render_pdf(image_path, work) if image_path.suffix.lower() == ".pdf" else image_path


class RemoteBoxDetector:
    """Sends the image to the bounding-box service (bbox-final/server.py) and returns its boxes."""

    def __init__(self, url: str, timeout: float = 300):
        self.url, self.timeout = url.rstrip("/"), timeout

    def warm_up(self):
        try:
            with urllib.request.urlopen(f"{self.url}/health", timeout=5) as r:
                h = json.load(r)
            print(f"bounding-box service {self.url}: " + ("ready" if h.get("models_loaded") else
                                                          "reachable, models still loading" if h.get("ok") else
                                                          f"error: {h.get('error')}"))
        except Exception as e:
            print(f"bounding-box service {self.url} not reachable yet ({type(e).__name__}: {e})")

    def detect(self, image_path) -> dict:
        image_path = Path(image_path)
        with tempfile.TemporaryDirectory() as d:
            src = _prepared(image_path, Path(d))
            boundary = uuid.uuid4().hex
            head = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{src.name}"\r\n'
                    f"Content-Type: application/octet-stream\r\n\r\n").encode()
            body = head + src.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(f"{self.url}/detect", data=body, method="POST",
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                ann = json.load(r)
        except urllib.error.HTTPError as e:
            try:
                detail = json.load(e).get("detail")
            except Exception:
                detail = e.reason
            if isinstance(detail, dict):
                detail = detail.get("error", detail)
            raise RuntimeError(f"bounding-box service: HTTP {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise RuntimeError(f"bounding-box service {self.url} unreachable: {getattr(e, 'reason', e)}") from None
        ann["image_name"] = image_path.name
        ann["source"] = f"bbox-final int8 @ {self.url}"
        return ann


class LocalBoxDetector:
    """Runs the models in this process (heavy: torch + ultralytics + openvino)."""

    def __init__(self, bbox_dir: Path = BBOX_DIR, device: str = "cpu"):
        self.bbox_dir, self.device = Path(bbox_dir), device
        self._predictor = None
        self._lock = threading.Lock()

    def _load(self):
        if self._predictor is None:
            if not (self.bbox_dir / "bbox_runtime").is_dir():
                raise RuntimeError(f"bbox-final not found at {self.bbox_dir} (set LITHO_BBOX_DIR)")
            if str(self.bbox_dir) not in sys.path:
                sys.path.insert(0, str(self.bbox_dir))
            from bbox_runtime import BoundingBoxPredictor

            with redirect_stdout(sys.stderr):
                self._predictor = BoundingBoxPredictor(device=self.device, cpu_threads=None)
        return self._predictor

    def warm_up(self):
        with self._lock:
            self._load()

    def detect(self, image_path) -> dict:
        image_path = Path(image_path)
        with self._lock, tempfile.TemporaryDirectory() as d, redirect_stdout(sys.stderr):
            ann = self._load().predict(_prepared(image_path, Path(d)))
        ann["image_name"] = image_path.name
        ann["source"] = "bbox-final int8 (local)"
        return ann


def _from_env():
    url = os.environ.get("LITHO_BBOX_URL", "").strip()
    if url:
        return RemoteBoxDetector(url, float(os.environ.get("LITHO_BBOX_TIMEOUT", "300")))
    if os.environ.get("LITHO_BBOX_LOCAL", "") in ("1", "true", "yes"):
        return LocalBoxDetector()
    return None  # load_detector() turns this into "no detector"


detector = _from_env()
