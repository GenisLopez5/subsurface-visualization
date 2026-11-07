"""
Background jobs for the API: single logs and batch folders.

One extraction runs at a time (OCR already uses every CPU core); other jobs queue behind a lock.
State lives in memory, so it is lost when the server restarts (results on disk are kept).
"""
from __future__ import annotations

import shutil
import threading
import traceback
import uuid
from pathlib import Path

import numpy as np

from . import batch as B
from . import drawing, pipeline
from .config import Settings


def depth_to_y(result, depth: float) -> float:
    c = result["depth_calibration"]
    if c["model"] == "piecewise_linear":
        lab = sorted((l["depth"], l["y"]) for l in c["labels"] if l["used"])
        d, y = np.array(lab).T
        if d[0] <= depth <= d[-1]:
            return float(np.interp(depth, d, y))
    return (depth - c["intercept"]) / c["depth_per_px"]


class Job:
    def __init__(self, image_path, annotation_path, force=False, notes=()):
        self.id = uuid.uuid4().hex[:12]
        self.image_path, self.annotation_path = Path(image_path), Path(annotation_path)
        self.force = force
        self.status = "queued"  # queued -> running -> done | error
        self.log: list[str] = list(notes)
        self.error: str | None = None
        self.result: dict | None = None
        self.cache_file: Path | None = None
        self.tmp: Path | None = None
        self.page: Path | None = None
        self.image = None  # PIL image of the page (kept for zooms)
        self.overview: Path | None = None
        self.overview_layout: dict | None = None
        self.finished = threading.Event()


class JobManager:
    def __init__(self, settings: Settings):
        self.s = settings
        self.jobs: dict[str, Job] = {}
        self.batches: dict[str, dict] = {}
        self.lock = threading.Lock()

    # ---------------- single log ----------------

    def submit(self, image_path, annotation_path, force=False, notes=()) -> Job:
        job = Job(image_path, annotation_path, force, notes)
        self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def _run(self, job: Job):
        log = job.log.append
        try:
            if self.lock.locked():
                log("queued")
            with self.lock:
                job.status = "running"
                job.result, job.cache_file = pipeline.extract_cached(
                    job.image_path, job.annotation_path, self.s.cache, force=job.force, log=log)
                job.tmp = self.s.tmp / job.id
                job.tmp.mkdir(parents=True, exist_ok=True)
                drawing.write_results_csv(job.result["intervals"], job.tmp / "results.csv")
                job.page, job.image = pipeline.page_for_notebook(job.image_path, job.result, job.tmp, log)
                log("drawing")
                job.overview = drawing.draw(job.page, job.annotation_path, job.result,
                                            drawing.default_scale(job.image.height), job.tmp, tif=True)
                job.overview_layout = drawing.read_layout(job.overview)
                log(f"{len(job.result['intervals'])} intervals")
                job.status = "done"
        except Exception as e:
            job.status, job.error = "error", f"{type(e).__name__}: {e}"
            log(traceback.format_exc())
        finally:
            job.finished.set()

    def annotated_image(self, job: Job, scale: float | None = None, y_range=None, tif=False) -> Path:
        """Draw (part of) the job's log. Returns the PNG preview path (`scale` sets its size), or with
        tif=True the full-resolution TIF."""
        if tif:
            out = job.tmp / "annotated_full.tif"
            if not out.exists():
                drawing.render_tif(job.image, job.annotation_path, job.result, out, y_range=y_range)
            return out
        scale = scale or drawing.default_scale(job.image.height)
        return drawing.draw(job.page, job.annotation_path, job.result, scale, job.tmp,
                            page_image=job.image, y_range=y_range)

    def zoom(self, job: Job, top_depth: float, bottom_depth: float, scale=0.5) -> Path:
        ya, yb = sorted((depth_to_y(job.result, top_depth), depth_to_y(job.result, bottom_depth)))
        yb = min(yb, ya + 20000)  # keep the picture a sane size
        # PNG for the browser; a TIF is written next to it (same name, .tif) as the saved result
        png = drawing.draw(job.page, job.annotation_path, job.result, scale, job.tmp,
                           page_image=job.image, y_range=(ya, yb), tif=True)
        return png

    def cleanup(self, job: Job):
        if job.tmp:
            shutil.rmtree(job.tmp, ignore_errors=True)

    # ---------------- batch folder ----------------

    def start_batch(self, folder, force=False) -> dict:
        scan = B.scan_folder(folder)
        if not scan["pairs"]:
            raise ValueError("No JSON/image pairs found")
        if any(b["status"] == "running" for b in self.batches.values()):
            raise ValueError("A batch is already running")
        bj = {"id": uuid.uuid4().hex[:12], "status": "running", "scan": scan, "log": [], "stop": False,
              "error": None, "items": [{"log": p["name"], "status": "waiting"} for p in scan["pairs"]]}
        self.batches[bj["id"]] = bj

        def on_item(i, row):
            bj["items"][i] = row

        def work():
            try:
                B.run_batch(scan, self.s.cache, self.s.tmp, force=force, log=bj["log"].append, on_item=on_item,
                            should_stop=lambda: bj["stop"], item_lock=self.lock)
                bj["status"] = "stopped" if bj["stop"] else "done"
            except Exception as e:
                bj["status"], bj["error"] = "error", f"{type(e).__name__}: {e}"
                bj["log"].append(traceback.format_exc())

        threading.Thread(target=work, daemon=True).start()
        return bj
