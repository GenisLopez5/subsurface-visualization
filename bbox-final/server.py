#!/usr/bin/env python3
"""
Bounding-box service: the bbox-final workflow (int8 OpenVINO models) behind HTTP, so a lighter machine
can send it images instead of running the models itself.

    POST /detect   multipart `file` (.tif/.tiff/.png/.jpg) -> {image_name, width, height, bounding_boxes{
                   depth_column, interpreted_lithology_column, lithology_legend}}, [xmin, ymin, xmax, ymax]
                   in original-image pixels. 422 when the three regions can't be resolved.
    GET  /health   {"ok": true, "models_loaded": bool, "workflow_version": ...}

PDFs are rendered to an image by the client (the lithology app) before they are sent.

Run:  ./run_server.sh            (or: python server.py --host 0.0.0.0 --port 8100)
Test: curl -F file=@log.tif http://<this machine>:8100/detect
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager, redirect_stdout
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bbox_runtime import BoundingBoxPredictor  # noqa: E402
from bbox_runtime.inference import InferenceError  # noqa: E402

ALLOWED = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}
MAX_MB = int(os.environ.get("BBOX_MAX_UPLOAD_MB", "500"))

_state: dict = {"predictor": None, "error": None}
_lock = threading.Lock()  # one prediction at a time: the models are shared and the CPU is busy anyway


def _load():
    with _lock:
        if _state["predictor"] is None:
            try:
                with redirect_stdout(sys.stderr):
                    _state["predictor"] = BoundingBoxPredictor(device=os.environ.get("BBOX_DEVICE", "auto"),
                                                               cpu_threads=None)
                _state["error"] = None
            except Exception as e:
                _state["error"] = f"{type(e).__name__}: {e}"
                raise
        return _state["predictor"]


def _predict(path: Path) -> dict:
    predictor = _load()
    with _lock, redirect_stdout(sys.stderr):
        return predictor.predict(path)


@asynccontextmanager
async def lifespan(_app):
    def warm_up():
        try:
            _load()
            print("models loaded, ready")
        except Exception as e:
            print(f"could not load the models: {_state['error']}", file=sys.stderr)
    threading.Thread(target=warm_up, daemon=True).start()  # load the models now, not on the first request
    yield


app = FastAPI(title="Bounding-box service", version="1.0.0", lifespan=lifespan,
              description="Mud-log image -> depth column, lithology column and legend boxes (bbox-final, int8).")


@app.get("/health")
def health():
    p = _state["predictor"]
    return {"ok": _state["error"] is None, "models_loaded": p is not None,
            "workflow_version": getattr(p, "version", None), "device": getattr(p, "device", None),
            "error": _state["error"]}


@app.post("/detect")
async def detect(file: UploadFile = File(..., description="log image: .tif/.tiff/.png/.jpg")):
    name = Path(file.filename or "image.tif").name
    if Path(name).suffix.lower() not in ALLOWED:
        raise HTTPException(415, f"unsupported file type {Path(name).suffix!r} (send {', '.join(sorted(ALLOWED))};"
                                 " render PDFs to an image first)")
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / name
        with open(path, "wb") as fh:
            shutil.copyfileobj(file.file, fh)
        if path.stat().st_size > MAX_MB * 1024 * 1024:
            raise HTTPException(413, f"file larger than {MAX_MB} MB")
        started = time.perf_counter()
        try:
            result = await run_in_threadpool(_predict, path)
        except InferenceError as e:
            raise HTTPException(422, {"error": str(e), "partial_boxes": e.partial_boxes})
        except (ValueError, OSError) as e:
            raise HTTPException(400, f"{type(e).__name__}: {e}")
        except Exception as e:
            raise HTTPException(500, f"{type(e).__name__}: {e}")
    result["image_name"] = name
    result["seconds"] = round(time.perf_counter() - started, 2)
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="Serve the bounding-box workflow over HTTP")
    ap.add_argument("--host", default="0.0.0.0", help="0.0.0.0 = reachable from other machines (default)")
    ap.add_argument("--port", type=int, default=8100)
    a = ap.parse_args(argv)
    import uvicorn

    print(f"bounding-box service on http://{a.host}:{a.port}  (docs: /docs, test: POST /detect)")
    uvicorn.run(app, host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
