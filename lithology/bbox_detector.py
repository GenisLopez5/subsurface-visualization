"""
Automatic bounding boxes (depth column, lithology column, legend) with the bbox-final models:
two YOLO11s detectors exported to OpenVINO int8, run on the CPU.

Plugs into the app as a `BoxDetector` (see annotations.py): LITHO_DETECTOR=lithology.bbox_detector:detector
(the default). The models live in bbox-final/ next to this package; LITHO_BBOX_DIR points elsewhere.
They are loaded once, on first use, and shared by every request (one prediction at a time).
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
from contextlib import redirect_stdout
from pathlib import Path

BBOX_DIR = Path(os.environ.get("LITHO_BBOX_DIR") or Path(__file__).resolve().parent.parent / "bbox-final")
PDF_DPI = 200  # PDFs are rendered at this resolution for detection; the JSON's width/height record it


class Int8BoxDetector:
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
        """Image (.tif/.png/.jpg/.pdf) -> {image_name, width, height, bounding_boxes{...}} in original pixels."""
        image_path = Path(image_path)
        with self._lock:
            predictor = self._load()
            with redirect_stdout(sys.stderr):
                if image_path.suffix.lower() != ".pdf":
                    ann = predictor.predict(image_path)
                else:
                    with tempfile.TemporaryDirectory() as d:
                        ann = predictor.predict(_render_pdf(image_path, Path(d)))
        ann["image_name"] = image_path.name
        ann["source"] = "bbox-final int8"
        return ann


def _render_pdf(pdf: Path, out_dir: Path) -> Path:
    """First page as a PNG at PDF_DPI. extract.py later renders the page at the JSON's width, i.e. the same grid."""
    import pypdfium2 as pdfium

    page = pdfium.PdfDocument(str(pdf))[0]
    out = out_dir / f"{pdf.stem}.png"
    page.render(scale=PDF_DPI / 72).to_pil().convert("RGB").save(out)
    return out


detector = Int8BoxDetector()
