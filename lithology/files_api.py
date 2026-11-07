"""
/api/v1: file management for a separate frontend (uploads, status, results), stored in Postgres.

    POST   /api/v1/uploads                       one or more images (+ optional JSONs / annotation data)
    GET    /api/v1/batches                       uploads, newest first, with counts per status
    GET    /api/v1/batches/{id}                  one upload and its files
    DELETE /api/v1/batches/{id}
    GET    /api/v1/files                         ?status=&batch_id=&q=&limit=&offset=
    GET    /api/v1/files/{id}                    status, which outputs exist, stats, links
    GET    /api/v1/status?ids=a,b,c              light status of many files (for polling)
    GET    /api/v1/files/{id}/download/{kind}    original | annotation | result_tif | result_csv | result_json
    GET    /api/v1/files/{id}/preview/{kind}     PNG of original | result_tif for <img> (?width=)
    PUT    /api/v1/files/{id}/annotation         set/replace the bounding boxes, then (re)process
    POST   /api/v1/files/{id}/reprocess          compute again (?force=true ignores the extraction cache)
    DELETE /api/v1/files/{id}                    any status (a running job's results are discarded)
    DELETE /api/v1/files?ids=a,b,c               several at once
    GET    /api/v1/health

File status: uploading -> queued (or needs_annotation) -> processing -> done | error
(uploading: the request has returned, the image is being stored in Postgres in the background)
"""
from __future__ import annotations

import io
import json
import shutil
import threading
import uuid
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from .annotations import (IMAGE_EXT, AnnotationStore, BoxDetector, annotation_matches, resolve_upload_annotation,
                          validate_annotation)
from .config import Settings
from .db import BLOB_KINDS, DB, STATUSES, dumps
from .worker import CONTENT_TYPES, Uploader, Worker, safe_name, spool_dir

MAX_JSON_BYTES = 5 * 1024 * 1024

Kind = Literal["original", "annotation", "result_tif", "result_csv", "result_json"]
PREVIEW_KINDS = ("original", "result_tif")


# ------------------------------------------------------------------ response models

class BlobInfo(BaseModel):
    filename: str
    size: int
    content_type: str
    created_at: datetime
    url: str


class Outputs(BaseModel):
    original: bool
    annotation: bool
    result_tif: bool
    result_csv: bool
    result_json: bool


class FileOut(BaseModel):
    id: UUID
    batch_id: UUID
    name: str
    status: Literal["uploading", "needs_annotation", "queued", "processing", "done", "error"]
    computed: bool  # result TIF and CSV are both stored
    outputs: Outputs
    files: dict[str, BlobInfo]
    preview: dict[str, str]
    annotation_source: str | None = None
    note: str | None = None
    error: str | None = None
    stats: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class StatusOut(BaseModel):
    id: UUID
    name: str
    status: str
    computed: bool
    error: str | None = None
    updated_at: datetime


class DeleteOut(BaseModel):
    deleted: list[UUID]
    not_found: list[UUID]


class FileList(BaseModel):
    total: int
    items: list[FileOut]


class BatchOut(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    file_count: int
    counts: dict[str, int]
    done: bool  # nothing left uploading, queued or processing
    files: list[FileOut] | None = None


class Rejected(BaseModel):
    filename: str
    reason: str


class UploadOut(BaseModel):
    batch: BatchOut
    files: list[FileOut]
    rejected: list[Rejected]
    unused_annotations: list[str]  # uploaded JSONs that matched none of the uploaded images


class Health(BaseModel):
    ok: bool
    db: str
    worker_alive: bool
    queued: int | None = None
    uploads_pending: int | None = None
    processing: dict | None = None


# ------------------------------------------------------------------ helpers

def file_out(row: dict) -> FileOut:
    fid = row["id"]
    blobs = row.get("blobs") or {}
    base = f"/api/v1/files/{fid}"
    files = {k: BlobInfo(**v, url=f"{base}/download/{k}") for k, v in blobs.items()}
    outputs = Outputs(**{k: k in blobs for k in BLOB_KINDS})
    preview = {k: f"{base}/preview/{k}" for k in PREVIEW_KINDS if k in blobs}
    return FileOut(**{k: row[k] for k in ("id", "batch_id", "name", "status", "annotation_source", "note", "error",
                                          "stats", "created_at", "updated_at", "started_at", "finished_at")},
                   computed=outputs.result_tif and outputs.result_csv, outputs=outputs, files=files, preview=preview)


def batch_out(row: dict, files: list[dict] | None = None) -> BatchOut:
    counts = {k: v for k, v in (row.get("counts") or {}).items()}
    return BatchOut(id=row["id"], name=row["name"], created_at=row["created_at"], file_count=sum(counts.values()),
                    counts=counts, done=not any(counts.get(k) for k in ("uploading", "queued", "processing")),
                    files=[file_out(f) for f in files] if files is not None else None)


def _provided_for(provided, image_name: str, n_images: int):
    """Pick this image's annotation from the `annotations` form field.
    Accepted shapes: one annotation object (when one image is uploaded, or matched by its image_name),
    {"<image file name>": annotation, ...}, or [annotation, ...] matched by image_name."""
    if provided is None:
        return None
    if isinstance(provided, dict) and "bounding_boxes" in provided:
        return provided if n_images == 1 or annotation_matches(provided, image_name) else None
    if isinstance(provided, dict):
        for key, ann in provided.items():
            if Path(key).name == image_name or annotation_matches({"image_name": key}, image_name):
                return ann
        return None
    if isinstance(provided, list):
        for ann in provided:
            if isinstance(ann, dict) and annotation_matches(ann, image_name):
                return ann
    return None


class PreviewCache:
    def __init__(self, max_items=48):
        self.items: OrderedDict = OrderedDict()
        self.max, self.lock = max_items, threading.Lock()

    def get(self, key):
        with self.lock:
            if key in self.items:
                self.items.move_to_end(key)
                return self.items[key]

    def put(self, key, value):
        with self.lock:
            self.items[key] = value
            self.items.move_to_end(key)
            while len(self.items) > self.max:
                self.items.popitem(last=False)


def render_preview(blob: dict, width: int) -> bytes:
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    data = blob["data"]
    if blob["filename"].lower().endswith(".pdf"):
        import pypdfium2 as pdfium

        page = pdfium.PdfDocument(bytes(data))[0]
        w = page.get_width()
        im = page.render(scale=max(width / w, 0.05)).to_pil()
    else:
        im = Image.open(io.BytesIO(data))
        im.draft("RGB", (width, int(im.height * width / im.width)))  # fast path for JPEGs
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB") if im.mode != "1" else im.convert("L")
    if im.width > width:
        im = im.resize((width, max(1, round(im.height * width / im.width))), Image.Resampling.LANCZOS
                       if im.width * im.height < 80_000_000 else Image.Resampling.BOX)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=False)
    return buf.getvalue()


# ------------------------------------------------------------------ router

def make_router(settings: Settings, state: dict, store: AnnotationStore, detector: BoxDetector | None) -> APIRouter:
    """state holds "db" (DB | None), "worker" (Worker | None) and "db_error" (str | None),
    filled in when the app starts."""
    r = APIRouter(prefix="/api/v1", tags=["files (v1)"])
    previews = PreviewCache()
    max_bytes = settings.max_upload_mb * 1024 * 1024

    def get_db() -> DB:
        if state.get("db") is None:
            raise HTTPException(503, state.get("db_error") or "database not configured (set DATABASE_URL)")
        return state["db"]

    def notify():
        if state.get("worker"):
            state["worker"].notify()

    def file_or_404(db: DB, fid: UUID) -> dict:
        row = db.get_file(fid)
        if not row:
            raise HTTPException(404, "unknown file")
        return row

    @r.get("/health", response_model=Health)
    def health():
        db, w = state.get("db"), state.get("worker")
        if db is None:
            return Health(ok=False, db=state.get("db_error") or "not configured", worker_alive=False)
        try:
            db.ping()
            q = db.queue_length()
        except Exception as e:
            return Health(ok=False, db=f"error: {type(e).__name__}: {e}", worker_alive=bool(w and w.alive))
        cur = w.current if w else None
        up = state.get("uploader")
        return Health(ok=bool(w and w.alive), db="ok", worker_alive=bool(w and w.alive), queued=q,
                      uploads_pending=up.pending if up else None,
                      processing={"id": cur["id"], "name": cur["name"], "last_log": (cur["log"] or [""])[-1]}
                      if cur else None)

    # ---------------- uploads

    @r.post("/uploads", response_model=UploadOut, status_code=201,
            summary="Upload one image or a batch; each image is queued for processing")
    def upload(files: list[UploadFile] = File(..., description="log images (.tif/.tiff/.pdf/.png/.jpg) and, "
                                                               "optionally, their bounding-box JSONs"),
               name: str | None = Form(None, description="batch name (default: the first file name)"),
               annotations: str | None = Form(None, description="bounding boxes as JSON data, e.g. from an "
                                              "extraction algorithm: one annotation object, "
                                              "{\"<image file name>\": annotation}, or a list matched by image_name"),
               db: DB = Depends(get_db)):
        try:
            provided = json.loads(annotations) if annotations else None
        except json.JSONDecodeError as e:
            raise HTTPException(422, f"annotations is not valid JSON: {e}")

        scratch = settings.tmp / "v1-upload" / uuid.uuid4().hex
        scratch.mkdir(parents=True, exist_ok=True)
        try:
            images, jsons, rejected = [], [], []
            for f in files:
                if not f or not f.filename:
                    continue
                fname = Path(f.filename).name
                ext = Path(fname).suffix.lower()
                if ext == ".json":
                    data = f.file.read(MAX_JSON_BYTES + 1)
                    try:
                        if len(data) > MAX_JSON_BYTES:
                            raise ValueError("file too large")
                        jsons.append((fname, validate_annotation(json.loads(data))))
                    except Exception as e:
                        rejected.append(Rejected(filename=fname, reason=f"invalid annotation JSON: {e}"))
                    continue
                if ext not in IMAGE_EXT:
                    rejected.append(Rejected(filename=fname, reason=f"unsupported file type {ext or '(none)'}"))
                    continue
                path = scratch / f"{len(images)}" / safe_name(fname)  # stream to disk, never all in memory
                path.parent.mkdir()
                size = 0
                with open(path, "wb") as out:
                    while chunk := f.file.read(1 << 20):
                        size += len(chunk)
                        if size > max_bytes:
                            break
                        out.write(chunk)
                if size > max_bytes:
                    rejected.append(Rejected(filename=fname, reason=f"larger than {settings.max_upload_mb} MB"))
                elif size == 0:
                    rejected.append(Rejected(filename=fname, reason="empty file"))
                else:
                    images.append((fname, path))
            if not images:
                raise HTTPException(422, {"error": "no image in the upload",
                                          "rejected": [x.model_dump() for x in rejected]})

            batch = db.create_batch(name or (images[0][0] if len(images) == 1
                                             else f"{images[0][0]} + {len(images) - 1} more"))
            created, used_json = [], set()
            for fname, path in images:
                try:
                    ann, source, note = resolve_upload_annotation(
                        fname, path, jsons, _provided_for(provided, fname, len(images)), store, detector)
                except ValueError as e:
                    ann, source, note = None, None, f"provided annotation rejected: {e}"
                if source == "uploaded":
                    used_json.add(note)
                blobs = {}
                if ann is not None:
                    blobs["annotation"] = (f"{Path(fname).stem}.json", "application/json", dumps(ann))
                fid = uuid.uuid4()
                dest = spool_dir(settings, fid)  # the image goes to Postgres in the background
                dest.mkdir(parents=True)
                shutil.move(path, dest / path.name)
                created.append(db.create_file(batch["id"], fname, "uploading", annotation_source=source,
                                              note=note or None, blobs=blobs, file_id=fid))
                state["uploader"].submit(fid, CONTENT_TYPES.get(Path(fname).suffix.lower()))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        return UploadOut(batch=batch_out(db.get_batch(batch["id"])), files=[file_out(f) for f in created],
                         rejected=rejected, unused_annotations=[n for n, _ in jsons if n not in used_json])

    # ---------------- batches

    @r.get("/batches", response_model=list[BatchOut])
    def list_batches(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), db: DB = Depends(get_db)):
        return [batch_out(b) for b in db.list_batches(limit, offset)]

    @r.get("/batches/{bid}", response_model=BatchOut)
    def get_batch(bid: UUID, db: DB = Depends(get_db)):
        b = db.get_batch(bid)
        if not b:
            raise HTTPException(404, "unknown batch")
        rows, _ = db.list_files(batch_id=bid, limit=10_000)
        return batch_out(b, sorted(rows, key=lambda x: x["name"]))

    @r.delete("/batches/{bid}", status_code=204, summary="Delete a batch with all its files and results")
    def delete_batch(bid: UUID, db: DB = Depends(get_db)):
        ids = db.delete_batch(bid)
        if ids is None:
            raise HTTPException(404, "unknown batch")
        drop_local(ids)
        return Response(status_code=204)

    # ---------------- files

    @r.get("/files", response_model=FileList)
    def list_files(status: list[str] | None = Query(None, description=f"any of {', '.join(STATUSES)}"),
                   batch_id: UUID | None = None, q: str | None = Query(None, description="name contains"),
                   limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), db: DB = Depends(get_db)):
        bad = [x for x in status or [] if x not in STATUSES]
        if bad:
            raise HTTPException(422, f"unknown status {bad}; use {list(STATUSES)}")
        rows, total = db.list_files(status, batch_id, q, limit, offset)
        return FileList(total=total, items=[file_out(x) for x in rows])

    @r.get("/status", response_model=list[StatusOut],
           summary="Light status check for many files at once: ?ids=<id>,<id>,... (unknown ids are left out)")
    def status(ids: str = Query(..., description="comma-separated file ids"), db: DB = Depends(get_db)):
        try:
            wanted = [UUID(x.strip()) for x in ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(422, "ids must be comma-separated file ids")
        if len(wanted) > 1000:
            raise HTTPException(422, "at most 1000 ids")
        return [StatusOut(**row) for row in db.statuses(wanted)]

    @r.get("/files/{fid}", response_model=FileOut)
    def get_file(fid: UUID, db: DB = Depends(get_db)):
        return file_out(file_or_404(db, fid))

    def drop_local(ids):
        for fid in ids:
            shutil.rmtree(spool_dir(settings, fid), ignore_errors=True)

    @r.delete("/files/{fid}", status_code=204,
              summary="Delete a file: original, annotation and results (any status; a running job is discarded)")
    def delete_file(fid: UUID, db: DB = Depends(get_db)):
        if not db.delete_files([fid]):
            raise HTTPException(404, "unknown file")
        drop_local([fid])
        return Response(status_code=204)

    @r.delete("/files", response_model=DeleteOut, summary="Delete several files: ?ids=<id>,<id>,...")
    def delete_files(ids: str = Query(..., description="comma-separated file ids"), db: DB = Depends(get_db)):
        try:
            wanted = [UUID(x.strip()) for x in ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(422, "ids must be comma-separated file ids")
        deleted = db.delete_files(wanted)
        drop_local(deleted)
        gone = set(deleted)
        return DeleteOut(deleted=deleted, not_found=[x for x in wanted if x not in gone])

    @r.get("/files/{fid}/download/{kind}", responses={200: {"content": {"application/octet-stream": {}}}})
    def download(fid: UUID, kind: Kind, inline: bool = Query(False, description="show in the browser instead "
                                                                              "of downloading"),
                 db: DB = Depends(get_db)):
        b = db.get_blob(fid, kind)
        if not b:
            file_or_404(db, fid)
            raise HTTPException(404, f"{kind} not available yet")
        disp = "inline" if inline else "attachment"
        return Response(bytes(b["data"]), media_type=b["content_type"],
                        headers={"Content-Disposition": f'{disp}; filename="{b["filename"]}"', "ETag": b["sha256"]})

    @r.get("/files/{fid}/preview/{kind}", responses={200: {"content": {"image/png": {}}}})
    def preview(fid: UUID, kind: Literal["original", "result_tif"], request: Request,
                width: int = Query(1200, ge=64, le=4000), db: DB = Depends(get_db)):
        meta = db.blob_meta(fid, kind)
        if not meta:
            file_or_404(db, fid)
            raise HTTPException(404, f"{kind} not available yet")
        etag = f'"{meta["sha256"][:16]}-{width}"'
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304)
        key = (str(fid), kind, width, meta["sha256"])
        png = previews.get(key)
        if png is None:
            png = render_preview(db.get_blob(fid, kind), width)
            previews.put(key, png)
        return Response(png, media_type="image/png", headers={"ETag": etag, "Cache-Control": "private, max-age=3600"})

    @r.put("/files/{fid}/annotation", response_model=FileOut,
           summary="Set or replace the bounding boxes (JSON body, or a JSON file as multipart 'file'); re-queues")
    async def put_annotation(fid: UUID, request: Request, db: DB = Depends(get_db),
                             source: Literal["uploaded", "provided", "detector"] | None = Query(
                                 None, description="where the boxes come from (default: 'uploaded' for a file, "
                                                   "'provided' for a JSON body)")):
        file_or_404(db, fid)
        ctype = request.headers.get("content-type", "")
        try:
            if ctype.startswith("multipart/"):
                form = await request.form()
                up = form.get("file")
                if up is None or not hasattr(up, "read"):
                    raise HTTPException(422, "multipart upload needs a 'file' field")
                ann, src = json.loads(await up.read()), source or "uploaded"
            else:
                ann, src = json.loads(await request.body()), source or "provided"
            validate_annotation(ann)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(422, f"invalid annotation: {e}")
        name = file_or_404(db, fid)["name"]
        try:
            db.set_annotation(fid, dumps(ann), f"{Path(name).stem}.json", src)
        except ValueError as e:
            raise HTTPException(409, str(e))
        notify()
        return file_out(db.get_file(fid))

    @r.post("/files/{fid}/reprocess", response_model=FileOut)
    def reprocess(fid: UUID, force: bool = Query(False, description="ignore the cached extraction"),
                  db: DB = Depends(get_db)):
        try:
            db.requeue(fid, force)
        except LookupError:
            raise HTTPException(404, "unknown file")
        except ValueError as e:
            raise HTTPException(409, str(e))
        notify()
        return file_out(db.get_file(fid))

    return r


def setup(app, settings: Settings, lock: threading.Lock, store: AnnotationStore):
    """Mount /api/v1 and hook DB + worker start/stop into the app's lifespan."""
    from .annotations import load_detector

    state: dict = {"db": None, "worker": None, "uploader": None, "db_error": None}
    detector = None
    try:
        detector = load_detector(settings.detector)
    except Exception as e:
        print(f"LITHO_DETECTOR could not be loaded ({type(e).__name__}: {e}); continuing without it")
    app.include_router(make_router(settings, state, store, detector))
    app.state.v1 = state

    def start():
        if not settings.database_url:
            state["db_error"] = "database not configured (set DATABASE_URL)"
            print("/api/v1 disabled: DATABASE_URL is not set")
            return
        try:
            db = DB(settings.database_url)
            db.open()
        except Exception as e:
            state["db_error"] = f"database unavailable: {type(e).__name__}"
            print(f"/api/v1 disabled: could not connect to the database ({type(e).__name__}: {e})")
            return
        state["db"] = db
        state["worker"] = Worker(db, settings, lock)
        state["uploader"] = Uploader(db, settings, on_stored=state["worker"].notify)
        state["worker"].start()
        state["uploader"].start()
        print("/api/v1 ready (database connected, worker started)")

    def stop():
        if state["uploader"]:
            state["uploader"].stop()
        if state["worker"]:
            state["worker"].stop()
        if state["db"]:
            state["db"].close()

    return start, stop
