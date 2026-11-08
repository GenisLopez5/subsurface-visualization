"""
Postgres storage for the /api/v1 file API (works with any Postgres, e.g. Tiger Data).

    litho_files     one row per log image: status, where its annotation came from, stats, error
    litho_blobs     the files themselves (bytea): original, annotation, result_tif, result_csv, result_json,
                    legend_original (legend cut from the original), legend_generated (the colour key)

Which results exist is read from litho_blobs, so "is it computed?" is always the truth on disk.
"""
from __future__ import annotations

import hashlib
import json
import uuid

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

STATUSES = ("uploading", "needs_annotation", "queued", "processing", "done", "error")
RESULT_KINDS = ("result_tif", "result_csv", "result_json", "legend_original", "legend_generated")
BLOB_KINDS = ("original", "annotation", *RESULT_KINDS)

SCHEMA = """
CREATE TABLE IF NOT EXISTS litho_files (
    id                 uuid PRIMARY KEY,
    name               text NOT NULL,
    status             text NOT NULL,
    annotation_source  text CHECK (annotation_source IN ('uploaded','provided','dataset','detector')),
    note               text,
    error              text,
    force              boolean NOT NULL DEFAULT false,
    stats              jsonb,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    started_at         timestamptz,
    finished_at        timestamptz
);
ALTER TABLE litho_files DROP CONSTRAINT IF EXISTS litho_files_status_check;
ALTER TABLE litho_files ADD CONSTRAINT litho_files_status_check
    CHECK (status IN ('uploading','needs_annotation','queued','processing','done','error'));
CREATE INDEX IF NOT EXISTS litho_files_status_idx ON litho_files (status, created_at);
-- files used to be grouped into upload batches: drop that from older databases (the files stay)
ALTER TABLE litho_files DROP COLUMN IF EXISTS batch_id;
DROP TABLE IF EXISTS litho_batches;
CREATE TABLE IF NOT EXISTS litho_blobs (
    file_id       uuid NOT NULL REFERENCES litho_files(id) ON DELETE CASCADE,
    kind          text NOT NULL CHECK (kind IN ('original','annotation','result_tif','result_csv','result_json')),
    filename      text NOT NULL,
    content_type  text NOT NULL,
    size          bigint NOT NULL,
    sha256        text NOT NULL,
    data          bytea NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (file_id, kind)
);
-- new blob kinds (the two legends): widen the kind check on older databases
ALTER TABLE litho_blobs DROP CONSTRAINT IF EXISTS litho_blobs_kind_check;
ALTER TABLE litho_blobs ADD CONSTRAINT litho_blobs_kind_check
    CHECK (kind IN ('original','annotation','result_tif','result_csv','result_json','legend_original','legend_generated'));
"""

# file row + which blobs it has (never the blob data itself)
_FILE_COLS = """
    f.id, f.name, f.status, f.annotation_source, f.note, f.error, f.stats,
    f.created_at, f.updated_at, f.started_at, f.finished_at,
    COALESCE((SELECT jsonb_object_agg(b.kind, jsonb_build_object('filename', b.filename, 'size', b.size,
              'content_type', b.content_type, 'created_at', b.created_at))
              FROM litho_blobs b WHERE b.file_id = f.id), '{}'::jsonb) AS blobs
"""


class DB:
    def __init__(self, url: str, min_size=1, max_size=5):
        self.pool = ConnectionPool(url, min_size=min_size, max_size=max_size, open=False,
                                   kwargs={"row_factory": dict_row, "connect_timeout": 15})

    def open(self):
        self.pool.open(wait=True, timeout=30)
        with self.pool.connection() as c:
            c.execute(SCHEMA)

    def close(self):
        self.pool.close()

    def ping(self) -> bool:
        with self.pool.connection() as c:
            return c.execute("SELECT 1 AS ok").fetchone()["ok"] == 1

    # ---------------- files

    def create_file(self, name: str, status: str, annotation_source=None, note=None, error=None,
                    blobs: dict[str, tuple[str, str, bytes]] | None = None, file_id=None) -> dict:
        """blobs: {kind: (filename, content_type, data)}; everything in one transaction."""
        fid = file_id or uuid.uuid4()
        with self.pool.connection() as c, c.transaction():
            c.execute("""INSERT INTO litho_files (id, name, status, annotation_source, note, error)
                         VALUES (%s, %s, %s, %s, %s, %s)""",
                      (fid, name, status, annotation_source, note, error))
            for kind, blob in (blobs or {}).items():
                self._put_blob(c, fid, kind, *blob)
        return self.get_file(fid)

    def get_file(self, file_id) -> dict | None:
        with self.pool.connection() as c:
            return c.execute(f"SELECT {_FILE_COLS} FROM litho_files f WHERE f.id = %s", (file_id,)).fetchone()

    def list_files(self, status=None, q=None, limit=100, offset=0) -> tuple[list[dict], int]:
        where, args = [], []
        if status:
            where.append("f.status = ANY(%s)")
            args.append(list(status))
        if q:
            where.append("f.name ILIKE %s")
            args.append(f"%{q}%")
        w = ("WHERE " + " AND ".join(where)) if where else ""
        with self.pool.connection() as c:
            total = c.execute(f"SELECT count(*) AS n FROM litho_files f {w}", args).fetchone()["n"]
            rows = c.execute(f"SELECT {_FILE_COLS} FROM litho_files f {w} ORDER BY f.created_at DESC, f.name "
                             f"LIMIT %s OFFSET %s", [*args, limit, offset]).fetchall()
        return rows, total

    def file_names(self, ids=None) -> list[dict]:
        """id, name and status of the given files (all files when ids is None), oldest first."""
        w, args = ("WHERE id = ANY(%s)", (list(ids),)) if ids is not None else ("", ())
        with self.pool.connection() as c:
            return c.execute(f"SELECT id, name, status FROM litho_files {w} ORDER BY created_at, name", args).fetchall()

    def statuses(self, ids) -> list[dict]:
        with self.pool.connection() as c:
            return c.execute("""
                SELECT f.id, f.name, f.status, f.error, f.updated_at,
                       EXISTS (SELECT 1 FROM litho_blobs b WHERE b.file_id = f.id AND b.kind = 'result_tif')
                   AND EXISTS (SELECT 1 FROM litho_blobs b WHERE b.file_id = f.id AND b.kind = 'result_csv') AS computed
                FROM litho_files f WHERE f.id = ANY(%s)""", (list(ids),)).fetchall()

    def rename_file(self, file_id, name: str) -> bool:
        with self.pool.connection() as c:
            return c.execute("UPDATE litho_files SET name = %s, updated_at = now() WHERE id = %s RETURNING id",
                             (name, file_id)).fetchone() is not None

    def delete_files(self, ids) -> list:
        """Delete files (any status) with their blobs. Returns the ids that existed."""
        with self.pool.connection() as c:
            rows = c.execute("DELETE FROM litho_files WHERE id = ANY(%s) RETURNING id", (list(ids),)).fetchall()
            return [r["id"] for r in rows]

    def set_annotation(self, file_id, data: bytes, filename: str, source: str, note: str = "") -> bool:
        """Store (replace) the annotation, drop old results and queue the file."""
        with self.pool.connection() as c, c.transaction():
            row = c.execute("SELECT status FROM litho_files WHERE id = %s FOR UPDATE", (file_id,)).fetchone()
            if not row:
                return False
            if row["status"] == "processing":
                raise ValueError("file is being processed; try again when it has finished")
            self._put_blob(c, file_id, "annotation", filename, "application/json", data)
            c.execute("DELETE FROM litho_blobs WHERE file_id = %s AND kind = ANY(%s)", (file_id, list(RESULT_KINDS)))
            c.execute("""UPDATE litho_files SET status = %s, annotation_source = %s, note = %s, error = NULL,
                         stats = NULL, started_at = NULL, finished_at = NULL, updated_at = now() WHERE id = %s""",
                      ("uploading" if row["status"] == "uploading" else "queued", source, note, file_id))
        return True

    def requeue(self, file_id, force=False) -> str:
        """Drop results and queue again. Returns the new status (or raises LookupError/ValueError)."""
        with self.pool.connection() as c, c.transaction():
            row = c.execute("SELECT status FROM litho_files WHERE id = %s FOR UPDATE", (file_id,)).fetchone()
            if not row:
                raise LookupError("unknown file")
            if row["status"] in ("processing", "uploading"):
                raise ValueError(f"file is {row['status']}")
            has_ann = c.execute("SELECT 1 FROM litho_blobs WHERE file_id = %s AND kind = 'annotation'",
                                (file_id,)).fetchone()
            status = "queued" if has_ann else "needs_annotation"
            c.execute("DELETE FROM litho_blobs WHERE file_id = %s AND kind = ANY(%s)", (file_id, list(RESULT_KINDS)))
            c.execute("""UPDATE litho_files SET status = %s, force = %s, error = NULL, stats = NULL,
                         started_at = NULL, finished_at = NULL, updated_at = now() WHERE id = %s""",
                      (status, force, file_id))
            return status

    def store_original(self, file_id, filename, content_type, data: bytes) -> str | None:
        """Background upload finished: store the image and move the file out of 'uploading'
        (to 'queued' if it has an annotation, else 'needs_annotation'). None if the file was deleted."""
        with self.pool.connection() as c, c.transaction():
            row = c.execute("SELECT status FROM litho_files WHERE id = %s FOR UPDATE", (file_id,)).fetchone()
            if not row:
                return None
            self._put_blob(c, file_id, "original", filename, content_type, data)
            has_ann = c.execute("SELECT 1 FROM litho_blobs WHERE file_id = %s AND kind = 'annotation'",
                                (file_id,)).fetchone()
            status = "queued" if has_ann else "needs_annotation"
            c.execute("UPDATE litho_files SET status = %s, updated_at = now() WHERE id = %s AND status = 'uploading'",
                      (status, file_id))
            return status

    def uploading(self) -> list[dict]:
        with self.pool.connection() as c:
            return c.execute("SELECT id, name FROM litho_files WHERE status = 'uploading' ORDER BY created_at").fetchall()

    # ---------------- blobs

    @staticmethod
    def _put_blob(c, file_id, kind, filename, content_type, data: bytes):
        c.execute("""INSERT INTO litho_blobs (file_id, kind, filename, content_type, size, sha256, data)
                     VALUES (%s, %s, %s, %s, %s, %s, %s)
                     ON CONFLICT (file_id, kind) DO UPDATE SET filename = EXCLUDED.filename,
                       content_type = EXCLUDED.content_type, size = EXCLUDED.size, sha256 = EXCLUDED.sha256,
                       data = EXCLUDED.data, created_at = now()""",
                  (file_id, kind, filename, content_type, len(data), hashlib.sha256(data).hexdigest(), data))

    def get_blob(self, file_id, kind) -> dict | None:
        with self.pool.connection() as c:
            return c.execute("SELECT * FROM litho_blobs WHERE file_id = %s AND kind = %s", (file_id, kind)).fetchone()

    def blob_meta(self, file_id, kind) -> dict | None:
        with self.pool.connection() as c:
            return c.execute("SELECT file_id, kind, filename, content_type, size, sha256, created_at FROM litho_blobs "
                             "WHERE file_id = %s AND kind = %s", (file_id, kind)).fetchone()

    # ---------------- worker queue

    def reset_stuck(self) -> int:
        """Files left 'processing' by a crash or restart go back to the queue."""
        with self.pool.connection() as c:
            return c.execute("UPDATE litho_files SET status = 'queued', started_at = NULL, updated_at = now() "
                             "WHERE status = 'processing'").rowcount

    def claim_next(self) -> dict | None:
        with self.pool.connection() as c:
            return c.execute("""
                UPDATE litho_files SET status = 'processing', started_at = now(), updated_at = now(), error = NULL
                WHERE id = (SELECT id FROM litho_files WHERE status = 'queued' ORDER BY created_at, name
                            FOR UPDATE SKIP LOCKED LIMIT 1)
                RETURNING id, name, force""").fetchone()

    def finish(self, file_id, stats: dict, results: dict[str, tuple[str, str, bytes]]):
        with self.pool.connection() as c, c.transaction():
            if not c.execute("""UPDATE litho_files SET status = 'done', stats = %s, force = false,
                                finished_at = now(), updated_at = now() WHERE id = %s""",
                             (Jsonb(stats), file_id)).rowcount:
                return  # deleted while it was being processed: drop the results
            for kind, blob in results.items():
                self._put_blob(c, file_id, kind, *blob)

    def fail(self, file_id, error: str):
        with self.pool.connection() as c:
            c.execute("""UPDATE litho_files SET status = 'error', error = %s, force = false, finished_at = now(),
                         updated_at = now() WHERE id = %s""", (error, file_id))

    def queue_length(self) -> int:
        with self.pool.connection() as c:
            return c.execute("SELECT count(*) AS n FROM litho_files WHERE status = 'queued'").fetchone()["n"]


def dumps(obj) -> bytes:
    return json.dumps(obj, indent=2).encode()
