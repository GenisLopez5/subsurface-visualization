"""
Background worker for the /api/v1 file API: takes queued files from Postgres one at a time,
runs the pipeline (extraction -> CSV + full-resolution TIF) and stores the results back.

The queue is the litho_files table itself (status = 'queued', claimed with FOR UPDATE SKIP LOCKED),
so it survives restarts and several server processes could share it.
"""
from __future__ import annotations

import contextlib
import queue
import re
import shutil
import threading
import traceback
from pathlib import Path

from . import header_meta, pipeline
from .config import Settings
from .db import DB, dumps

CONTENT_TYPES = {".tif": "image/tiff", ".tiff": "image/tiff", ".pdf": "application/pdf", ".png": "image/png",
                 ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def spool_dir(settings: Settings, file_id) -> Path:
    """Local copy of an uploaded image: written by the upload request, pushed to Postgres by the
    Uploader, used by the Worker (so it needn't download it again), removed after processing."""
    return settings.data_dir / "v1-spool" / str(file_id)


def spooled(settings: Settings, file_id) -> Path | None:
    d = spool_dir(settings, file_id)
    files = [p for p in d.iterdir() if p.is_file()] if d.is_dir() else []
    return files[0] if files else None


class Uploader:
    """Stores spooled uploads in Postgres in the background, so the upload request returns at once
    (status 'uploading' until the image is in the database)."""

    def __init__(self, db: DB, settings: Settings, on_stored=lambda: None):
        self.db, self.s, self.on_stored = db, settings, on_stored
        self.q: "queue.Queue" = queue.Queue()
        self.thread: threading.Thread | None = None

    def start(self):
        for row in self.db.uploading():  # left over from a restart
            if spooled(self.s, row["id"]):
                self.q.put((row["id"], None))
            else:
                self.db.fail(row["id"], "upload interrupted (server restarted before the image was stored); "
                                        "upload it again")
        self.thread = threading.Thread(target=self._loop, name="litho-uploader", daemon=True)
        self.thread.start()

    def submit(self, file_id, content_type: str | None = None):
        self.q.put((file_id, content_type))

    def stop(self):
        self.q.put(None)

    @property
    def pending(self) -> int:
        return self.q.qsize()

    def _loop(self):
        while (item := self.q.get()) is not None:
            fid, ctype = item
            path = spooled(self.s, fid)
            try:
                if path is None:
                    raise FileNotFoundError("spooled upload is missing")
                ctype = ctype or CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
                status = self.db.store_original(fid, path.name, ctype, path.read_bytes())
                if status is None:  # deleted meanwhile
                    shutil.rmtree(spool_dir(self.s, fid), ignore_errors=True)
                elif status == "queued":
                    self.on_stored()
            except Exception as e:
                traceback.print_exc()
                with contextlib.suppress(Exception):
                    self.db.fail(fid, f"storing the upload failed: {type(e).__name__}: {e}")


def safe_name(name: str) -> str:
    """ASCII-only name for files on the server's disk (work folders, spool)."""
    name = Path(name).name
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "file"


def item_name(stored: str) -> str:
    """An item's name for the frontend: the stored name (the upload's file name) without its extension.
    The extension stays in the database: it names the original image inside the ZIPs."""
    return Path(stored).stem or stored


def display_name(name: str) -> str:
    """The name as uploaded, for what users download (ZIP entries, folders): keeps spaces and accents,
    drops only what can't be in a file name (path separators, control and Windows-reserved characters)."""
    name = Path(str(name).replace("\\", "/")).name
    return re.sub(r'[\x00-\x1f\x7f<>:"/|?*]+', "_", name).strip(" .") or "file"


# Results are named after the uploaded image + "_solved", so they never clash with the original
RESULT_SUFFIXES = {"result_tif": "_solved.tif", "result_csv": "_solved.csv", "result_json": "_solved.json",
                   "legend_original": "_legend_original.png", "legend_generated": "_legend_generated.png",
                   "satellite_view": "_satellite_view.png"}


def result_filename(image_name: str, kind: str) -> str:
    return display_name(Path(image_name).stem) + RESULT_SUFFIXES[kind]


class Worker:
    def __init__(self, db: DB, settings: Settings, lock: threading.Lock | None = None, poll_seconds=5.0):
        self.db, self.s = db, settings
        self.lock = lock or threading.Lock()  # shared with the old UI jobs: one OCR run at a time
        self.poll = poll_seconds
        self.wake = threading.Event()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.current: dict | None = None  # {"id", "name", "log": [...]} while processing

    def start(self):
        n = self.db.reset_stuck()
        if n:
            print(f"worker: {n} file(s) left 'processing' by a restart were queued again")
        self.thread = threading.Thread(target=self._loop, name="litho-worker", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.wake.set()

    @property
    def alive(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def notify(self):
        self.wake.set()

    def _loop(self):
        while not self.stop_event.is_set():
            try:
                job = self.db.claim_next()
            except Exception:
                traceback.print_exc()
                job = None
            if job is None:
                self.wake.wait(self.poll)
                self.wake.clear()
                continue
            self._process(job)

    def _process(self, job: dict):
        fid, name = job["id"], job["name"]
        log: list[str] = []
        self.current = {"id": str(fid), "name": name, "log": log}
        work = self.s.tmp / "v1" / str(fid)  # stable path per file, so the extraction cache is reused
        try:
            annotation = self.db.get_blob(fid, "annotation")
            if annotation is None:
                raise RuntimeError("annotation is missing")
            work.mkdir(parents=True, exist_ok=True)
            local = spooled(self.s, fid)
            if local is not None:  # still on this machine from the upload: no need to download it
                img = local
            else:
                original = self.db.get_blob(fid, "original")
                if original is None:
                    raise RuntimeError("original image is missing")
                img = work / safe_name(original["filename"])
                img.write_bytes(original["data"])
            ann = work / "annotation.json"
            ann.write_bytes(annotation["data"])
            out = work / "out"
            stem = Path(img.name).stem
            with self.lock:
                r = pipeline.process(img, ann, out, stem, self.s.cache, self.s.tmp, force=bool(job["force"]),
                                     log=log.append)
            files = r["files"]
            results = {
                "result_tif": (result_filename(name, "result_tif"), "image/tiff", Path(files["tif"]).read_bytes()),
                "result_csv": (result_filename(name, "result_csv"), "text/csv", Path(files["csv"]).read_bytes()),
                "result_json": (result_filename(name, "result_json"), "application/json",
                                dumps({**r["result"], "layout": r["layout"], "header": r.get("header")})),
            }
            # the log's own legend, the colour key, the satellite view around the well (not in the downloads)
            for kind in ("legend_original", "legend_generated", "satellite_view"):
                if files.get(kind):
                    results[kind] = (result_filename(name, kind), "image/png", Path(files[kind]).read_bytes())
            stats = {k: r[k] for k in ("seconds", "intervals", "top_depth", "bottom_depth", "px_per_depth_unit",
                                       "depth_labels_used", "depth_fit_rms_px", "legend_entries")}
            stats["layout"] = r["layout"]  # result TIF = legend bar on top + the page: y shift for the UI
            stats["header"] = header_meta.summary(r.get("header"))  # date + lat/lon from the log's header
            stats["satellite"] = r.get("satellite")  # provider + attribution (credit line) + km of satellite_view
            try:
                self.db.finish(fid, stats, results)
            except Exception as e:
                if "satellite_view" not in results:
                    raise
                # the satellite view is optional: never lose the lithology result over it (e.g. a database
                # whose kind check doesn't know 'satellite_view' yet). finish() is one transaction: retry without.
                log.append(f"storing the satellite view failed ({type(e).__name__}: {e}); stored without it")
                results.pop("satellite_view")
                stats["satellite"] = None
                self.db.finish(fid, stats, results)
        except Exception as e:
            tail = "\n".join(log[-5:])
            msg = f"{type(e).__name__}: {e}"
            print(f"worker: {name} failed: {msg}\n{traceback.format_exc()}")
            with contextlib.suppress(Exception):
                self.db.fail(fid, msg + (f"\n{tail}" if tail else ""))
        finally:
            self.current = None
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(spool_dir(self.s, fid), ignore_errors=True)
