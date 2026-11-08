"""
HTTP API.

For other apps
    POST /api/process                 upload a log image (TIF/PDF/PNG) -> annotated TIF + CSV
                                      (bounding boxes: matching annotation JSON from the store;
                                       an annotation file can also be sent along)

For the web UI (asynchronous, with progress)
    POST /api/jobs                    start a job from uploaded file(s) or a dataset entry
    GET  /api/jobs/{id}               status, log, summary, intervals
    POST /api/jobs/{id}/rerun         same inputs, ignore the cache
    GET  /api/jobs/{id}/overview.png  whole log drawn by the notebook code
    GET  /api/jobs/{id}/zoom.png      ?top=&bottom= (depths) &scale=
    GET  /api/jobs/{id}/annotated.tif full resolution
    GET  /api/jobs/{id}/result.csv | result.json
    GET  /api/dataset                 annotations in the store (+ whether the image is available)
    POST /api/batch/scan              look inside a server folder
    POST /api/batch                   start a batch on a server folder
    GET  /api/batch/{id}              progress
    POST /api/batch/{id}/stop
    GET  /api/batch/{id}/picture/{i}

For frontends (files and results stored in Postgres; see files_api.py)
    /api/v1/...                       uploads, files, downloads, previews, health

Desktop helpers (only with --desktop, when the server runs on the user's machine)
    POST /api/desktop/pick-folder     native folder dialog (zenity)
    POST /api/desktop/open            open a file/folder in the default app

Errors are JSON: {"error": "..."} (+ "need": "json"|"image" and "keep" when a partner file is missing).
Interactive docs: /docs
Drawn images (overview, zoom, annotated TIF, v1 result TIF + preview) have the colour key added
ON TOP, so they are taller than the original. Their layout comes with them, as a "layout" object in
the JSON and as X-Legend-Height / X-Scale / X-Y-Start headers on the image responses:

    y_in_drawn_image = legend_height_px + (y_in_original - y_start) * scale
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from .annotations import (IMAGE_EXT, AnnotationNotFound, AnnotationStore, ImageStore, box_source,
                          load_annotation, resolve_annotation)
from .config import Settings
from . import drawing
from .jobs import Job, JobManager


def err(message: str, status: int = 400, **extra):
    return JSONResponse(status_code=status, content={"error": message, **extra})


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or Settings.from_env()
    s.ensure_dirs()
    store = AnnotationStore(s.annotation_dirs)
    images = ImageStore(s.image_dirs)
    jobs = JobManager(s)
    hooks = {}

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        await run_in_threadpool(hooks["start"])  # DB connect + schema + worker (v1 file API)
        yield
        hooks["stop"]()

    app = FastAPI(title="Lithology extraction API", version="0.2.0", lifespan=lifespan,
                  description="Mud-log image -> lithology intervals (CSV) + annotated image.\n\n"
                              "**/api/v1**: file management for frontends (uploads, status, results in Postgres).")
    if s.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["*"], allow_headers=["*"],
                           allow_credentials="*" not in s.cors_origins, expose_headers=["Content-Disposition", "ETag", *drawing.LAYOUT_HEADERS])
    from . import files_api
    from .annotations import load_detector

    detector = None
    try:
        detector = load_detector(s.detector)
    except Exception as e:
        print(f"LITHO_DETECTOR could not be loaded ({type(e).__name__}: {e}); continuing without it")
    print(f"bounding boxes by default: {s.box_source} "
          + ("(the matching dataset JSON; detection only with boxes=detector)" if s.box_source == "dataset"
             else "(automatic detection, dataset JSONs as fallback)"))
    if detector is not None:
        print(f"bounding-box detector: {s.detector}")
        if hasattr(detector, "warm_up"):  # load the models in the background, not on the first upload
            import threading
            threading.Thread(target=detector.warm_up, daemon=True).start()
    else:
        print("bounding boxes: automatic detection off (set LITHO_BBOX_URL to the bbox service); "
              "uploaded / dataset JSONs only")
    hooks["start"], hooks["stop"] = files_api.setup(app, s, jobs.lock, store, detector)
    from . import legend_review

    app.include_router(legend_review.make_router(s, store, images, jobs.lock))
    app.state.settings, app.state.jobs = s, jobs

    # ------------------------------------------------------------------ helpers

    def save_uploads(files: list[UploadFile]) -> list[Path]:
        if not files:
            return []
        d = s.uploads / uuid.uuid4().hex[:12]
        d.mkdir(parents=True)
        out = []
        for f in files:
            if not f or not f.filename:
                continue
            p = d / Path(f.filename).name
            with open(p, "wb") as fh:
                shutil.copyfileobj(f.file, fh)
            out.append(p)
        return out

    def upload_token(p: Path) -> str:
        return str(p.relative_to(s.uploads))

    def from_token(token: str) -> Path | None:
        p = (s.uploads / token).resolve()
        return p if p.is_relative_to(s.uploads.resolve()) and p.is_file() else None

    def inside(p: Path, roots) -> bool:
        p = p.resolve()
        return any(p.is_relative_to(Path(r).resolve()) for r in roots)

    BOXES_DOC = ("where the bounding boxes come from when no JSON is sent: `dataset` (the matching JSON from "
                 "the annotation folders) or `detector` (automatic detection, dataset JSON as fallback). "
                 f"Default: the server's LITHO_BOX_SOURCE ({s.box_source})")

    def pick_inputs(paths: list[Path], boxes: str | None = None):
        """(image, annotation, notes) from a list of files, finding the missing partner.
        Returns a JSONResponse on failure."""
        try:
            source = box_source(boxes, s.box_source)
        except ValueError as e:
            return err(str(e))
        img = ann_path = None
        notes = []
        for p in paths:
            if p.suffix.lower() == ".json":
                ann_path = p
            elif p.suffix.lower() in IMAGE_EXT:
                img = p
            else:
                return err(f"Unsupported file type: {p.name}")
        if img and not ann_path:
            try:
                ann_path, how = resolve_annotation(img, store, detector, source=source)
            except AnnotationNotFound as e:
                return err(str(e), need="json", keep=upload_token(img) if img.is_relative_to(s.uploads) else None)
            notes.append(f"annotation: {ann_path.name}" + (f" ({how})" if how else ""))
        try:
            ann = load_annotation(ann_path)
        except Exception:
            return err(f"Not an annotation file: {Path(ann_path).name}")
        if not img:
            img = ImageStore(s.image_dirs + [Path(ann_path).parent]).find(ann)
            if img is None:
                return err(f"Image not found: {ann.get('image_name')}", need="image",
                           keep=upload_token(ann_path) if ann_path.is_relative_to(s.uploads) else None)
            notes.append(f"image: {img.name}")
        return img, ann_path, notes

    def job_or_404(jid) -> Job | JSONResponse:
        job = jobs.jobs.get(jid)
        return job if job else err("Unknown job", 404)

    def job_json(job: Job) -> dict:
        out = {"id": job.id, "status": job.status, "log": job.log, "error": job.error,
               "image": job.image_path.name, "annotation": job.annotation_path.name}
        if job.status == "done":
            r = job.result
            c = r["depth_calibration"]
            out["summary"] = {
                "image": job.image_path.name,
                "annotation": job.annotation_path.name,
                "depth_fit": f"{c['px_per_depth_unit']:.2f} px/unit, {c['n_labels_used']} labels, "
                             f"RMS {c['rms_residual_px']:.2f} px",
                "legend": [e["label"] + (f"  ({e['ignored']})" if e.get("ignored") else "") for e in r["legend"]],
                "n_intervals": len(r["intervals"]),
                "depth_range": [r["intervals"][0]["top_depth"], r["intervals"][-1]["bottom_depth"]] if r["intervals"] else None,
            }
            out["intervals"] = r["intervals"]
            out["layout"] = job.overview_layout  # of overview.png
            out["files"] = {
                "Overview image": str(job.overview.with_suffix(".tif").resolve()),
                "Source image": str(job.image_path.resolve()),
                "Annotation JSON": str(job.annotation_path.resolve()),
                "Result JSON": str(job.cache_file.resolve()),
                "Temp folder": str(job.tmp.resolve()),
            }
        return out

    # ------------------------------------------------------------------ for other apps

    @app.get("/api/health")
    def health():
        return {"ok": True, "annotations": len(store.all())}

    @app.post("/api/process", summary="Process one log image: returns the annotated TIF and the CSV",
              description="`output=zip`: the ZIP, with the annotated TIF's layout in the `X-Legend-Height` / "
                          "`X-Scale` / `X-Y-Start` headers. `output=json`: `{id, image, annotation, notes, "
                          "intervals, layout, csv, annotated_tif_url, csv_url}`, where `layout` is the annotated "
                          "TIF's `{legend_height_px, scale, y_start, original_width, original_height, image_width, "
                          "image_height}`.\n\n" + drawing.LAYOUT_DOC,
              responses={200: {"content": {"application/zip": {}, "application/json": {}},
                               "headers": drawing.LAYOUT_OPENAPI_HEADERS,
                               "description": "ZIP (layout in the headers) or JSON (with `layout`)"}})
    def process(file: UploadFile = File(..., description="log image: .tif/.tiff/.pdf/.png"),
                annotation: UploadFile | None = File(None, description="optional bounding-box JSON; "
                                                     "if omitted, the matching JSON from the store is used"),
                output: str = Query("zip", pattern="^(zip|json)$",
                                    description="zip: <name>_annotated.tif + <name>_lithology.csv; "
                                                "json: intervals + CSV text + links to the files"),
                force: bool = Query(False, description="ignore cached results"),
                boxes: Literal["dataset", "detector"] | None = Query(None, description=BOXES_DOC)):
        paths = save_uploads([file] + ([annotation] if annotation else []))
        picked = pick_inputs(paths, boxes)
        if isinstance(picked, JSONResponse):
            return picked
        img, ann_path, notes = picked
        job = jobs.submit(img, ann_path, force, notes)
        job.finished.wait()
        if job.status != "done":
            return err(job.error or "processing failed", 500, id=job.id)
        try:
            tif = jobs.annotated_image(job, tif=True)
        except ValueError as e:
            return err(str(e))
        stem = Path(img.name).stem
        if output == "json":
            return {"id": job.id, "image": img.name, "annotation": ann_path.name, "notes": notes,
                    "intervals": job.result["intervals"],
                    "layout": drawing.read_layout(tif),  # of the annotated TIF
                    "csv": (job.tmp / "results.csv").read_text(),
                    "annotated_tif_url": f"/api/jobs/{job.id}/annotated.tif",
                    "csv_url": f"/api/jobs/{job.id}/result.csv"}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(tif, f"{stem}_annotated.tif")
            z.write(job.tmp / "results.csv", f"{stem}_lithology.csv")
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/zip",
                                 headers={"Content-Disposition": f'attachment; filename="{stem}_lithology.zip"',
                                          "X-Job-Id": job.id, **drawing.layout_headers(drawing.read_layout(tif))})

    # ------------------------------------------------------------------ jobs (web UI)

    @app.post("/api/jobs")
    def start_job(file: list[UploadFile] = File(default=[]), keep: str | None = Form(None),
                  dataset: str | None = Form(None), force: str = Form("0"),
                  boxes: str | None = Form(None, description=BOXES_DOC)):
        paths = save_uploads(file)
        if keep and (p := from_token(keep)):
            paths.append(p)
        if dataset:
            p = Path(dataset)
            if not p.is_file() or not inside(p, s.annotation_dirs):
                return err("Unknown dataset entry")
            paths.append(p)
        if not paths:
            return err("No file")
        picked = pick_inputs(paths, boxes)
        if isinstance(picked, JSONResponse):
            return picked
        img, ann_path, notes = picked
        job = jobs.submit(img, ann_path, force == "1", notes)
        return {"id": job.id, "image": img.name, "annotation": ann_path.name}

    @app.get("/api/jobs/{jid}", description="Status, progress log, summary and intervals. When done, `layout` "
                                            "is the layout of `overview.png`.\n\n" + drawing.LAYOUT_DOC)
    def job_status(jid: str):
        job = job_or_404(jid)
        return job if isinstance(job, JSONResponse) else job_json(job)

    @app.post("/api/jobs/{jid}/rerun")
    def rerun(jid: str):
        old = job_or_404(jid)
        if isinstance(old, JSONResponse):
            return old
        job = jobs.submit(old.image_path, old.annotation_path, force=True)
        return {"id": job.id, "image": job.image_path.name}

    def _done(jid):
        job = job_or_404(jid)
        if isinstance(job, JSONResponse):
            return job
        return job if job.status == "done" else err("Not ready", 409)

    @app.get("/api/jobs/{jid}/overview.png", responses=drawing.image_response_doc("image/png"))
    def overview(jid: str):
        job = _done(jid)
        if isinstance(job, JSONResponse):
            return job
        return FileResponse(job.overview, media_type="image/png", headers=drawing.layout_headers(job.overview_layout))

    @app.get("/api/jobs/{jid}/zoom.png",
             responses=drawing.image_response_doc("image/png", "a strip of the page: X-Y-Start is its first row"))
    def zoom(jid: str, top: float, bottom: float, scale: float = Query(0.5, gt=0, le=1)):
        job = _done(jid)
        if isinstance(job, JSONResponse):
            return job
        png = jobs.zoom(job, top, bottom, scale)
        return FileResponse(png, media_type="image/png", headers={"X-File-Path": str(png.with_suffix(".tif").resolve()),
                                                                  **drawing.layout_headers(drawing.read_layout(png))})

    @app.get("/api/jobs/{jid}/annotated.tif", responses=drawing.image_response_doc("image/tiff"))
    def annotated_tif(jid: str):
        job = _done(jid)
        if isinstance(job, JSONResponse):
            return job
        try:
            tif = jobs.annotated_image(job, tif=True)
        except ValueError as e:
            return err(str(e))
        return FileResponse(tif, media_type="image/tiff", filename=f"{job.image_path.stem}_annotated.tif",
                            headers=drawing.layout_headers(drawing.read_layout(tif)))

    @app.get("/api/jobs/{jid}/result.{fmt}")
    def result_file(jid: str, fmt: str):
        job = _done(jid)
        if isinstance(job, JSONResponse):
            return job
        if fmt == "csv":
            return FileResponse(job.tmp / "results.csv", media_type="text/csv",
                                filename=f"{job.image_path.stem}_lithology.csv")
        if fmt == "json":
            return FileResponse(job.cache_file, media_type="application/json",
                                filename=f"{job.image_path.stem}_lithology.json")
        return err("Use result.csv or result.json", 404)

    @app.get("/api/dataset")
    def dataset():
        split = {}
        if s.split_file and s.split_file.exists():
            sp = json.loads(s.split_file.read_text())
            split = {n: "train" for n in sp.get("train", [])} | {n: "test" for n in sp.get("test", [])}
        return [{"path": str(p), "name": p.stem, "split": split.get(p.stem, "other"),
                 "image_found": images.find(ann) is not None} for p, ann in store.all()]

    # ------------------------------------------------------------------ batch

    @app.post("/api/batch/scan")
    def batch_scan(path: str = Form(...)):
        from .batch import scan_folder
        try:
            return scan_folder(path)
        except Exception as e:
            return err(str(e))

    @app.post("/api/batch")
    def batch_start(path: str = Form(...), force: str = Form("0")):
        try:
            bj = jobs.start_batch(path, force == "1")
        except Exception as e:
            return err(str(e))
        return {"id": bj["id"]}

    @app.get("/api/batch/{bid}")
    def batch_status(bid: str):
        bj = jobs.batches.get(bid)
        if not bj:
            return err("Unknown batch", 404)
        items = bj["items"]
        return {"status": bj["status"], "error": bj["error"], "log": bj["log"][-300:],
                "results_folder": bj["scan"]["results_folder"], "debug_folder": bj["scan"]["debug_folder"],
                "summary": str(Path(bj["scan"]["results_folder"]) / "summary.csv"),
                "n": len(items), "finished": sum(i["status"] not in ("waiting", "running") for i in items),
                "items": items}

    @app.post("/api/batch/{bid}/stop")
    def batch_stop(bid: str):
        if bid in jobs.batches:
            jobs.batches[bid]["stop"] = True
        return {"ok": True}

    @app.get("/api/batch/{bid}/picture/{i}")
    def batch_picture(bid: str, i: int):
        bj = jobs.batches.get(bid)
        tif = (bj["items"][i].get("files") or {}).get("tif") if bj and 0 <= i < len(bj["items"]) else None
        if not tif:
            return err("Not ready", 404)
        from PIL import Image  # browsers can't show TIFs: convert for viewing only
        buf = io.BytesIO()
        Image.open(tif).save(buf, "PNG")
        return Response(buf.getvalue(), media_type="image/png")

    # ------------------------------------------------------------------ desktop helpers

    @app.post("/api/desktop/pick-folder")
    def pick_folder(start: str = Form("")):
        if not s.desktop:
            return err("Desktop features are disabled (start the server with --desktop)", 403)
        title = "Choose the folder with the logs"
        start_dir = start if start and Path(start).is_dir() else str(Path.home())
        if shutil.which("zenity"):
            cmd = ["zenity", "--file-selection", "--directory", f"--title={title}",
                   f"--filename={start_dir.rstrip('/')}/"]
        else:  # Windows / macOS / no zenity: Tk's native dialog, in its own process (Tk wants the main thread)
            code = ("import sys, tkinter as tk; from tkinter import filedialog; r = tk.Tk(); r.withdraw(); "
                    "r.attributes('-topmost', True); "
                    "p = filedialog.askdirectory(title=sys.argv[1], initialdir=sys.argv[2], parent=r); "
                    "sys.exit(1) if not p else print(p)")
            cmd = [sys.executable, "-c", code, title, start_dir]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
        except OSError as e:
            return err(f"no folder dialog available: {e}")
        path = r.stdout.strip()
        return {"cancelled": True} if r.returncode != 0 or not path else {"path": str(Path(path))}

    @app.post("/api/desktop/open")
    def open_path(path: str = Form(...)):
        if not s.desktop:
            return err("Desktop features are disabled (start the server with --desktop)", 403)
        p = Path(path)
        roots = [s.data_dir, *s.annotation_dirs, *s.image_dirs] + [b["scan"]["folder"] for b in jobs.batches.values()]
        if not p.exists() or not inside(p, roots):
            return err("Path not allowed")
        if sys.platform == "win32":
            os.startfile(str(p))  # noqa: S606 - path checked against the allowed roots above
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(p)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True}

    # ------------------------------------------------------------------ static UI (optional)

    if s.ui_dir:
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=s.ui_dir, html=True), name="ui")
    else:
        @app.get("/", include_in_schema=False)
        def root():
            return Response("Lithology extraction API. See /docs", media_type="text/plain")

    return app
