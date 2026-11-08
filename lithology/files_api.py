"""
/api/v1: file management for a separate frontend (uploads, status, results), stored in Postgres.

    POST   /api/v1/uploads                       one or more images (+ optional JSONs / annotation data)
    GET    /api/v1/files                         ?status=&q=&limit=&offset=  newest first
    GET    /api/v1/files/{id}                    status, which outputs exist, stats, download/preview links
    GET    /api/v1/status?ids=a,b,c              light status of many files (for polling)
    GET    /api/v1/files/{id}/download           ZIP of the original, the result TIF and the result CSV
    GET    /api/v1/download?ids=a,b,c            ZIP with one folder per file, each holding those three
    GET    /api/v1/download/all                  the same for every file in the database
    GET    /api/v1/files/{id}/preview/{kind}     PNG of original | result_tif | legend_original |
                                                 legend_generated for <img> (?width=)
    PUT    /api/v1/files/{id}/annotation         set/replace the bounding boxes, then (re)process
    POST   /api/v1/files/{id}/reprocess          compute again (?force=true ignores the extraction cache)
    DELETE /api/v1/files/{id}                    any status (a running job's results are discarded)
    DELETE /api/v1/files?ids=a,b,c               several at once
    GET    /api/v1/health

The result TIF has the colour key added on top, so it is taller than the original. Its layout is in
stats.layout of the file; preview/result_tif also sends it as X-Legend-Height / X-Scale / X-Y-Start
headers (at the preview's size):

    y_in_result_tif = legend_height_px + y_in_original * scale

File status: uploading -> queued (or needs_annotation) -> processing -> done | error
(uploading: the request has returned, the image is being stored in Postgres in the background)
"""
from __future__ import annotations

import base64
import io
import json
import re
import shutil
import tempfile
import threading
import uuid
import zipfile
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .annotations import (IMAGE_EXT, AnnotationStore, BoxDetector, annotation_matches, resolve_upload_annotation,
                          validate_annotation)
from .config import Settings
from . import drawing
from .db import BLOB_KINDS, DB, STATUSES, dumps
from .worker import CONTENT_TYPES, Uploader, Worker, display_name, item_name, result_filename, safe_name, spool_dir

MAX_JSON_BYTES = 5 * 1024 * 1024

PREVIEW_KINDS = ("original", "result_tif", "legend_original", "legend_generated")


# ------------------------------------------------------------------ response models

class BlobInfo(BaseModel):
    filename: str
    size: int
    content_type: str
    created_at: datetime


class Outputs(BaseModel):
    original: bool
    annotation: bool
    result_tif: bool
    result_csv: bool
    result_json: bool
    legend_original: bool = False   # the log's own legend, cut from the original image (PNG)
    legend_generated: bool = False  # the colour key drawn on top of the result TIF (PNG)


class Layout(BaseModel):
    """Where the original image sits in the result TIF: y_tif = legend_height_px + (y_original - y_start) * scale."""
    model_config = ConfigDict(extra="allow")
    legend_height_px: int = Field(description="height of the colour key bar on top of the result TIF, in TIF px "
                                              "(the y shift)")
    scale: float = Field(description="result-TIF px per original-image px (1: full resolution)")
    y_start: int = Field(0, description="first original-image row drawn (0: the whole page)")
    original_width: int
    original_height: int
    image_width: int = Field(description="result TIF width")
    image_height: int = Field(description="result TIF height = legend_height_px + drawn page height")


class Stats(BaseModel):
    """Extraction summary of a processed file (other keys may be added)."""
    model_config = ConfigDict(extra="allow")
    layout: Layout | None = Field(None, description="layout of the result TIF: the y shift between it and the "
                                                    "original image")


class Offset(BaseModel):
    """How the result TIF lines up with the original image."""
    offset_y_px: int = Field(description="result TIF height - original height: the colour key bar added on top. "
                                         "y_result = offset_y_px + y_original * scale")
    offset_x_px: int = Field(0, description="always 0: nothing is added on the sides")
    scale: float = Field(description="result-TIF px per original px (1: same resolution)")
    original_width: int
    original_height: int
    result_width: int
    result_height: int
    legend_original_box: list[int] | None = Field(None, description="[x0, y0, x1, y1] of the original legend "
                                                                    "(legend_original) in the original image, px")


class ResultFile(BaseModel):
    filename: str
    content_type: str
    content: str | None = Field(None, description="the file as text (CSV)")
    base64: str | None = Field(None, description="the file as base64 (TIF, PNG)")


class ResultOut(BaseModel):
    id: UUID
    name: str
    offset: Offset
    csv: ResultFile
    result_tif: ResultFile
    legend_original: ResultFile | None = Field(None, description="PNG: the log's own legend, cut from the "
                                                                 "original image (null for older results)")
    legend_generated: ResultFile | None = Field(None, description="PNG: the colour key drawn on top of the result "
                                                                  "TIF, same width, height = offset_y_px (null "
                                                                  "for older results)")


class RenameIn(BaseModel):
    name: str = Field(description="the new name, without an extension", examples=["Well 12 - mud log"])


class FileOut(BaseModel):
    id: UUID
    name: str = Field(description="the item's name, without an extension")
    status: Literal["uploading", "needs_annotation", "queued", "processing", "done", "error"]
    computed: bool  # result TIF and CSV are both stored
    outputs: Outputs
    files: dict[str, BlobInfo]
    preview: dict[str, str]
    download: str | None = None  # ZIP of original + result TIF + result CSV, once all three exist
    annotation_source: str | None = None
    note: str | None = None
    error: str | None = None
    stats: Stats | None = None
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


class Rejected(BaseModel):
    filename: str
    reason: str


class UploadOut(BaseModel):
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
    files = {k: BlobInfo(**v) for k, v in blobs.items()}
    outputs = Outputs(**{k: k in blobs for k in BLOB_KINDS})
    preview = {k: f"{base}/preview/{k}" for k in PREVIEW_KINDS if k in blobs}
    computed = outputs.result_tif and outputs.result_csv
    return FileOut(**{k: row[k] for k in ("id", "status", "annotation_source", "note", "error",
                                          "stats", "created_at", "updated_at", "started_at", "finished_at")},
                   name=item_name(row["name"]),
                   computed=computed, outputs=outputs, files=files, preview=preview,
                   download=f"{base}/download" if computed and outputs.original else None)


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
    """PNG of the blob at most `width` px wide. Images are decoded and shrunk in strips of rows,
    so a log that is hundreds of millions of pixels never has to exist at full size in RGB."""
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    data = blob["data"]
    if blob["filename"].lower().endswith(".pdf"):
        import pypdfium2 as pdfium

        page = pdfium.PdfDocument(bytes(data))[0]
        w = page.get_width()
        im = page.render(scale=max(width / w, 0.05)).to_pil()
        if im.width > width:
            im = im.resize((width, max(1, round(im.height * width / im.width))), Image.Resampling.LANCZOS)
    else:
        im = Image.open(io.BytesIO(data))
        im.draft("RGB", (width, int(im.height * width / im.width)))  # fast path for JPEGs
        if im.width > width:
            strips = _deflate_rgb_strips(im, data) or _pil_strips(im)
            im = _shrink_strips(strips, im.width, im.height, width)
        elif im.mode not in ("RGB", "L"):
            im = im.convert("RGB") if im.mode != "1" else im.convert("L")
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=False)
    return buf.getvalue()


PREVIEW_STRIP_ROWS = 4096


def _pil_strips(im):
    """(y, image) pieces of any image, converted to RGB/L a strip at a time."""
    mode = "L" if im.mode in ("1", "L", "I;16", "I") else "RGB"
    for y in range(0, im.height, PREVIEW_STRIP_ROWS):
        yield y, im.crop((0, y, im.width, min(y + PREVIEW_STRIP_ROWS, im.height))).convert(mode)


def _deflate_rgb_strips(im, data):
    """(y, image) pieces of a strip-based, Deflate-compressed 8-bit RGB TIF (the result TIFs) read straight
    from the file, one TIF strip at a time (Pillow would decode the whole image first). None if the
    file is anything else."""
    import zlib

    from PIL import Image

    try:
        t = im.tag_v2
        if (im.format != "TIFF" or im.mode != "RGB" or t.get(259) not in (8, 32946) or t.get(284, 1) != 1
                or t.get(317, 1) != 1 or 322 in t or 273 not in t):
            return None
        offsets, counts, rps = t[273], t[279], int(t.get(278, im.height))
    except Exception:
        return None
    if isinstance(offsets, int):
        offsets, counts = (offsets,), (counts,)
    mv = memoryview(data)

    def gen():
        for i, (off, n) in enumerate(zip(offsets, counts)):
            y = i * rps
            rows = min(rps, im.height - y)
            raw = zlib.decompress(mv[off:off + n])
            yield y, Image.frombuffer("RGB", (im.width, rows), raw[:im.width * 3 * rows], "raw", "RGB", 0, 1)
    return gen()


def _shrink_strips(strips, w, h, width):
    """Shrink (y, image) row strips of a w x h image to `width` px wide, gathering them into blocks of
    ~PREVIEW_STRIP_ROWS rows so each resize sees enough context."""
    from PIL import Image

    k = width / w
    out = None
    block, block_y, block_rows = [], 0, 0

    def flush():
        nonlocal out, block, block_rows
        if not block:
            return
        mode = block[0].mode
        piece = block[0] if len(block) == 1 else Image.new(mode, (w, block_rows))
        if len(block) > 1:
            yy = 0
            for b in block:
                piece.paste(b, (0, yy))
                yy += b.height
        top, bottom = round(block_y * k), round((block_y + block_rows) * k)
        if out is None:
            out = Image.new(mode, (width, max(1, round(h * k))), "white")
        if bottom > top:
            out.paste(piece.resize((width, bottom - top), Image.Resampling.LANCZOS), (0, top))
        block, block_rows = [], 0

    for y, part in strips:
        if not block:
            block_y = y
        block.append(part)
        block_rows += part.height
        if block_rows >= PREVIEW_STRIP_ROWS:
            flush()
    flush()
    return out


# ------------------------------------------------------------------ router

BUNDLE_KINDS = ("original", "result_tif", "result_csv")


def _bundle_blobs(db: DB, fid) -> dict[str, dict] | None:
    """The original, result TIF and result CSV of a file, or None if any of them is missing."""
    blobs = {k: db.get_blob(fid, k) for k in BUNDLE_KINDS}
    return blobs if all(blobs.values()) else None


def _bundle_names(image_name: str, blobs: dict[str, dict]) -> dict[str, str]:
    """ZIP entry names, from the file's name as uploaded (spaces kept): <name>.<ext> for the original,
    <name>_solved.tif / <name>_solved.csv for the results. Named here, not from the stored blob names,
    so results computed before the _solved naming come out the same."""
    names = {}
    for k, b in blobs.items():
        if k == "original":
            ext = Path(b["filename"]).suffix or Path(image_name).suffix
            names[k] = display_name(Path(image_name).stem) + ext
        else:
            names[k] = result_filename(image_name, k)
    return names


def content_disposition(filename: str, disposition: str = "attachment") -> str:
    """Content-Disposition that survives any file name: an ASCII fallback plus the real name (RFC 6266),
    since HTTP headers can't carry non-Latin-1 text and a quote would end the filename early."""
    fallback = safe_name(filename)
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


def _write_bundle(z: zipfile.ZipFile, blobs: dict[str, dict], image_name: str, folder: str = ""):
    used: set[str] = set()
    names = _bundle_names(image_name, blobs)
    for k, b in blobs.items():
        name = names[k]
        if name in used:  # e.g. an original already named like a result
            name = f"{k}_{name}"
        used.add(name)
        # TIFs are already compressed: store them, deflate only the CSV
        method = zipfile.ZIP_DEFLATED if k == "result_csv" else zipfile.ZIP_STORED
        z.writestr(folder + name, bytes(b["data"]), compress_type=method)


def _zip_response(fh, filename: str) -> StreamingResponse:
    """Stream a finished ZIP from a temp file, closing (and so deleting) it afterwards."""
    size = fh.tell()
    fh.seek(0)

    def chunks():
        try:
            while chunk := fh.read(1 << 20):
                yield chunk
        finally:
            fh.close()

    return StreamingResponse(chunks(), media_type="application/zip", headers={
        "Content-Disposition": content_disposition(filename), "Content-Length": str(size)})


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
                      processing={"id": cur["id"], "name": item_name(cur["name"]), "last_log": (cur["log"] or [""])[-1]}
                      if cur else None)

    # ---------------- uploads

    @r.post("/uploads", response_model=UploadOut, status_code=201,
            summary="Upload one or more images; each becomes its own file and is queued for processing")
    def upload(files: list[UploadFile] = File(..., description="log images (.tif/.tiff/.pdf/.png/.jpg) and, "
                                                               "optionally, their bounding-box JSONs"),
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
                created.append(db.create_file(fname, "uploading", annotation_source=source,
                                              note=note or None, blobs=blobs, file_id=fid))
                state["uploader"].submit(fid, CONTENT_TYPES.get(Path(fname).suffix.lower()))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        return UploadOut(files=[file_out(f) for f in created],
                         rejected=rejected, unused_annotations=[n for n, _ in jsons if n not in used_json])

    # ---------------- files

    @r.get("/files", response_model=FileList)
    def list_files(status: list[str] | None = Query(None, description=f"any of {', '.join(STATUSES)}"),
                   q: str | None = Query(None, description="name contains"),
                   limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), db: DB = Depends(get_db)):
        bad = [x for x in status or [] if x not in STATUSES]
        if bad:
            raise HTTPException(422, f"unknown status {bad}; use {list(STATUSES)}")
        rows, total = db.list_files(status, q, limit, offset)
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
        return [StatusOut(**{**row, "name": item_name(row["name"])}) for row in db.statuses(wanted)]

    @r.get("/files/{fid}", response_model=FileOut)
    def get_file(fid: UUID, db: DB = Depends(get_db)):
        return file_out(file_or_404(db, fid))

    @r.patch("/files/{fid}", response_model=FileOut, summary="Rename an item",
             description="Body: `{\"name\": \"new name\"}`, without an extension (names are always returned "
                         "without one). Downloads are named after it from then on (`<name>_solved.tif`, ...). "
                         "Works in any status; results are kept.")
    def rename_file(fid: UUID, body: RenameIn, db: DB = Depends(get_db)):
        name = body.name.strip()
        if not name or len(name) > 200:
            raise HTTPException(422, "name must be 1 to 200 characters")
        if re.search(r'[\x00-\x1f\x7f/\\]', name):
            raise HTTPException(422, "name can't contain / \\ or control characters")
        stored = file_or_404(db, fid)["name"]
        db.rename_file(fid, name + Path(stored).suffix)  # keep the upload's extension, internally
        return file_out(file_or_404(db, fid))

    def result_layout(db: DB, fid) -> dict | None:
        """Layout of the stored result TIF (legend height on top, scale); None for results made before it existed."""
        row = db.get_file(fid)
        return ((row or {}).get("stats") or {}).get("layout")

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

    @r.get("/files/{fid}/download", responses={200: {"content": {"application/zip": {}}}},
           summary="Download the original image, the result TIF and the result CSV together as one ZIP")
    def download_bundle(fid: UUID, db: DB = Depends(get_db)):
        row = file_or_404(db, fid)
        blobs = {k: db.get_blob(fid, k) for k in BUNDLE_KINDS}
        missing = [k for k, b in blobs.items() if not b]
        if missing:
            raise HTTPException(404, f"not available yet: {', '.join(missing)}")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            _write_bundle(z, blobs, row["name"])
        zip_name = f"{display_name(Path(row['name']).stem)}_solved.zip"
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": content_disposition(zip_name)})

    def result_offset(db: DB, fid, tif_blob: dict) -> Offset:
        """The y offset of the result TIF over the original: from the stored layout, or for results made
        before layouts were stored, measured from the two images' sizes (v1 draws at scale 1)."""
        lay = result_layout(db, fid)
        if lay:
            return Offset(offset_y_px=lay["legend_height_px"], scale=lay["scale"],
                          original_width=lay["original_width"], original_height=lay["original_height"],
                          result_width=lay["image_width"], result_height=lay["image_height"],
                          legend_original_box=lay.get("legend_original_box"))
        orig = db.get_blob(fid, "original")
        if not orig:
            raise HTTPException(404, "original not available, cannot measure the offset")
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        with Image.open(io.BytesIO(bytes(orig["data"]))) as o, Image.open(io.BytesIO(bytes(tif_blob["data"]))) as t:
            (ow, oh), (tw, th) = o.size, t.size  # header only, pixels are not decoded
        return Offset(offset_y_px=th - oh, scale=tw / ow, original_width=ow, original_height=oh,
                      result_width=tw, result_height=th)

    @r.get("/files/{fid}/result",
           summary="The result TIF, the result CSV, the two legends and the offset between the result TIF and "
                   "the original",
           description="`format=zip` (default): `<name>_solved.zip` with `<name>_solved.tif`, `<name>_solved.csv`, "
                       "`<name>_offset.json`, `<name>_legend_original.png` (the log's own legend, cut from the "
                       "original image) and `<name>_legend_generated.png` (the colour key drawn on top of the "
                       "result TIF); the offset is also in the `X-Offset-Y` header. The legend PNGs are missing "
                       "for results computed before they existed (reprocess to get them).\n\n"
                       "`format=json`: everything in one JSON (`ResultOut`): the offset, the CSV as text and the "
                       "TIF and legend PNGs as base64 (about 4/3 of their size).\n\n"
                       "**The offset** is how many px the result TIF is taller than the original: the colour key "
                       "bar added on top. Rows of the CSV (`top_y`, `bottom_y`) are original-image px; in the "
                       "result TIF they are at `y_result = offset_y_px + y_original * scale` (`scale` is 1, no "
                       "x offset).\n\n404 until the result TIF and CSV exist.",
           responses={200: {"model": ResultOut, "content": {"application/zip": {}},
                            "headers": {"X-Offset-Y": {"description": "offset_y_px: result TIF height - original "
                                                                      "height (the colour key bar on top)",
                                                       "schema": {"type": "integer"}},
                                        **drawing.LAYOUT_OPENAPI_HEADERS}}})
    def get_result(fid: UUID, format: Literal["zip", "json"] = Query("zip", description="zip or json"),
                   db: DB = Depends(get_db)):
        row = file_or_404(db, fid)
        tif, csv = db.get_blob(fid, "result_tif"), db.get_blob(fid, "result_csv")
        if not (tif and csv):
            raise HTTPException(404, f"result not available yet (status: {row['status']})")
        off = result_offset(db, fid, tif)
        headers = {"X-Offset-Y": str(off.offset_y_px), "X-Legend-Height": str(off.offset_y_px),
                   "X-Scale": f"{off.scale:.8g}", "X-Y-Start": "0"}
        tif_name, csv_name = result_filename(row["name"], "result_tif"), result_filename(row["name"], "result_csv")
        legends = {k: b for k in ("legend_original", "legend_generated") if (b := db.get_blob(fid, k))}
        if format == "json":
            b64 = lambda data: base64.b64encode(bytes(data)).decode("ascii")  # noqa: E731
            return ResultOut(id=row["id"], name=item_name(row["name"]), offset=off,
                             csv=ResultFile(filename=csv_name, content_type="text/csv",
                                            content=bytes(csv["data"]).decode("utf-8")),
                             result_tif=ResultFile(filename=tif_name, content_type="image/tiff", base64=b64(tif["data"])),
                             **{k: ResultFile(filename=result_filename(row["name"], k), content_type="image/png",
                                              base64=b64(b["data"])) for k, b in legends.items()})
        stem = display_name(Path(row["name"]).stem)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(tif_name, bytes(tif["data"]), compress_type=zipfile.ZIP_STORED)
            z.writestr(csv_name, bytes(csv["data"]), compress_type=zipfile.ZIP_DEFLATED)
            z.writestr(f"{stem}_offset.json", off.model_dump_json(indent=2), compress_type=zipfile.ZIP_DEFLATED)
            for k, b in legends.items():  # PNGs are already compressed
                z.writestr(result_filename(row["name"], k), bytes(b["data"]), compress_type=zipfile.ZIP_STORED)
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": content_disposition(f"{stem}_solved.zip"), **headers})

    def zip_many(db: DB, rows: list[dict], zip_name: str) -> StreamingResponse:
        """One folder per file (original + result TIF + CSV). Files without all three are left out and
        listed in skipped.txt; 404 if none qualifies. Built in a temp file, so big exports don't sit in RAM."""
        fh = tempfile.TemporaryFile()
        try:
            folders: set[str] = set()
            skipped, added = [], 0
            with zipfile.ZipFile(fh, "w") as z:
                for row in rows:
                    blobs = _bundle_blobs(db, row["id"])
                    if blobs is None:
                        skipped.append(f"{item_name(row['name'])}\t{row['id']}\t{row['status']}")
                        continue
                    folder = display_name(Path(row["name"]).stem)
                    if folder in folders:  # same name uploaded twice
                        folder = f"{folder}_{str(row['id'])[:8]}"
                    folders.add(folder)
                    _write_bundle(z, blobs, row["name"], folder + "/")
                    added += 1
                if skipped:
                    z.writestr("skipped.txt", "files without a finished result (name, id, status):\n"
                               + "\n".join(skipped) + "\n", compress_type=zipfile.ZIP_DEFLATED)
            if not added:
                raise HTTPException(404, "none of these files has finished results yet")
        except BaseException:
            fh.close()
            raise
        return _zip_response(fh, zip_name)

    @r.get("/download", responses={200: {"content": {"application/zip": {}}}},
           summary="ZIP of several files: ?ids=<id>,<id>,... — one folder per file with the original, "
                   "the result TIF and the result CSV (unfinished files are listed in skipped.txt)")
    def download_many(ids: str = Query(..., description="comma-separated file ids"), db: DB = Depends(get_db)):
        try:
            wanted = list(dict.fromkeys(UUID(x.strip()) for x in ids.split(",") if x.strip()))
        except ValueError:
            raise HTTPException(422, "ids must be comma-separated file ids")
        if not wanted:
            raise HTTPException(422, "no ids given")
        if len(wanted) > 1000:
            raise HTTPException(422, "at most 1000 ids")
        rows = db.file_names(wanted)
        unknown = set(wanted) - {r_["id"] for r_ in rows}
        if unknown:
            raise HTTPException(404, f"unknown file ids: {', '.join(map(str, unknown))}")
        return zip_many(db, rows, "lithology_selection.zip")

    @r.get("/download/all", responses={200: {"content": {"application/zip": {}}}},
           summary="ZIP of every file in the database — one folder per file with the original, the result TIF "
                   "and the result CSV (unfinished files are listed in skipped.txt)")
    def download_all(db: DB = Depends(get_db)):
        return zip_many(db, db.file_names(), f"lithology_all_{datetime.now():%Y%m%d_%H%M}.zip")

    @r.get("/files/{fid}/preview/{kind}",
           description="PNG preview for an `<img>`. For `result_tif`, the layout headers are already scaled to "
                       "the preview's width.\n\n" + drawing.LAYOUT_DOC,
           responses={200: {"content": {"image/png": {}}, "headers": drawing.LAYOUT_OPENAPI_HEADERS,
                            "description": "PNG (layout headers for `result_tif` only, at the preview's size)"}})
    def preview(fid: UUID, kind: Literal["original", "result_tif", "legend_original", "legend_generated"],
                request: Request,
                width: int = Query(1200, ge=64, le=4000), db: DB = Depends(get_db)):
        meta = db.blob_meta(fid, kind)
        if not meta:
            file_or_404(db, fid)
            raise HTTPException(404, f"{kind} not available yet")
        etag = f'"{meta["sha256"][:16]}-{width}"'
        headers = {"ETag": etag, "Cache-Control": "private, max-age=3600"}
        if kind == "result_tif":
            headers.update(drawing.layout_headers(drawing.scaled_layout(result_layout(db, fid), width)))
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=headers)
        key = (str(fid), kind, width, meta["sha256"])
        png = previews.get(key)
        if png is None:
            png = render_preview(db.get_blob(fid, kind), width)
            previews.put(key, png)
        return Response(png, media_type="image/png", headers=headers)

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


def setup(app, settings: Settings, lock: threading.Lock, store: AnnotationStore,
          detector: BoxDetector | None = None):
    """Mount /api/v1 and hook DB + worker start/stop into the app's lifespan."""
    state: dict = {"db": None, "worker": None, "uploader": None, "db_error": None}
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
