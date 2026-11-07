"""
Legend review: look at a log's legend next to what `parse_legend` extracted from it, and write down
what went wrong. Used by webui/legend.html.

    GET  /api/legend/logs                    dataset logs (+ whether the image exists, review status)
    GET  /api/legend/{name}?method=&force=   legend parsing debug output for that log (cached on disk);
                                             method = "old" (extract.parse_legend) or "grid" (legend_grid)
    GET  /api/legend/{name}/crop.png         the legend crop, as a PNG
    GET  /api/legend/{name}/review           saved review (or {})
    PUT  /api/legend/{name}/review           save a review (JSON body)
    GET  /api/legend/reviews                 all saved reviews in one list (to hand to whoever fixes it)

Files: <data-dir>/legend_review/{name}.{method}.json, {name}.crop.png, {name}.review.json
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Body, Query
from fastapi.responses import FileResponse, JSONResponse

from . import extract as X
from .legend_grid import parse_legend_grid

METHODS = {"old": X.parse_legend, "grid": parse_legend_grid}
from .annotations import AnnotationStore, ImageStore

BLANK_LIKE = "looks blank (symbol, not a lithology pattern)"


def make_router(settings, store: AnnotationStore, images: ImageStore, lock: threading.Lock) -> APIRouter:
    r = APIRouter(prefix="/api/legend", tags=["legend review"])
    out_dir = settings.data_dir / "legend_review"
    out_dir.mkdir(parents=True, exist_ok=True)

    def safe(name: str) -> str:
        return re.sub(r"[^\w.#\- ]", "_", name)

    def find(name: str):
        for p, ann in store.all():
            if p.stem == name:
                return p, ann
        return None, None

    def err(msg, status=400):
        return JSONResponse(status_code=status, content={"error": msg})

    def compute(name, ann_path, ann, method):
        img_path = images.find(ann) or ImageStore([ann_path.parent]).find(ann)
        if img_path is None:
            raise FileNotFoundError(f"image not found for {name}")
        t0 = time.time()
        img = X.load_page(img_path, ann)
        box = ann["bounding_boxes"]["lithology_legend"]
        dbg: dict = {}
        with lock:
            entries = METHODS[method](img, box, debug=dbg)
        from PIL import Image
        Image.fromarray(X.crop_rgb(img, box)).save(out_dir / f"{safe(name)}.crop.png")
        lx0, ly0 = box[0], box[1]
        seen = set()
        out_entries = []
        for e in entries:
            f = e["feature"]
            ignored = None
            if f[3] < 0.015 and np.linalg.norm(f[0:3] - 1.0) < 0.1:
                ignored = BLANK_LIKE
            elif e["label"] in seen:
                ignored = "duplicate label"
            seen.add(e["label"])
            sb = e["swatch_bbox"]
            out_entries.append({
                "label": e["label"], "raw_text": e["raw_text"], "ignored": ignored,
                "swatch": [sb[0] - lx0, sb[1] - ly0, sb[2] - lx0, sb[3] - ly0],
                "fill": [round(float(v), 3) for v in f[0:3]], "ink": round(float(f[3]), 4),
                "edge": round(float(f[4]), 4), "orient": [round(float(v), 3) for v in f[5:9]],
            })
        # which pairs of entries look alike to the column classifier (could be confused)?
        feats = [e["feature"] for e in entries]
        close = []
        for i in range(len(feats)):
            for j in range(i + 1, len(feats)):
                d = float(X.feature_distance(feats[i][None, :], feats[j])[0])
                close.append({"a": i, "b": j, "dist": round(d, 3)})
        close.sort(key=lambda c: c["dist"])
        data = {"name": name, "method": method, "image": img_path.name, "annotation": str(ann_path), "seconds": round(time.time() - t0, 1),
                "computed_at": time.strftime("%Y-%m-%d %H:%M:%S"), "entries": out_entries,
                "closest_pairs": close[:12], "debug": dbg}
        (out_dir / f"{safe(name)}.{method}.json").write_text(json.dumps(data, indent=1))
        return data

    @r.get("/logs")
    def logs():
        split = {}
        if settings.split_file and settings.split_file.exists():
            sp = json.loads(settings.split_file.read_text())
            split = {n: "train" for n in sp.get("train", [])} | {n: "test" for n in sp.get("test", [])}
        out = []
        for p, ann in store.all():
            rv = out_dir / f"{safe(p.stem)}.review.json"
            status = json.loads(rv.read_text()).get("status") if rv.exists() else None
            out.append({"name": p.stem, "split": split.get(p.stem, "other"), "image_found": images.find(ann) is not None,
                        "computed": [m for m in METHODS if (out_dir / f"{safe(p.stem)}.{m}.json").exists()],
                        "review": status})
        return out

    @r.get("/reviews")
    def all_reviews():
        out = []
        for f in sorted(out_dir.glob("*.review.json")):
            out.append(json.loads(f.read_text()))
        return out

    @r.get("/{name}/crop.png")
    def crop(name: str):
        f = out_dir / f"{safe(name)}.crop.png"
        return FileResponse(f, media_type="image/png") if f.exists() else err("not computed yet", 404)

    @r.get("/{name}/review")
    def get_review(name: str):
        f = out_dir / f"{safe(name)}.review.json"
        return json.loads(f.read_text()) if f.exists() else {}

    @r.put("/{name}/review")
    def put_review(name: str, body: dict = Body(...)):
        p, _ = find(name)
        if p is None:
            return err("unknown log", 404)
        method = body.get("method") if body.get("method") in METHODS else "old"
        dbg = out_dir / f"{safe(name)}.{method}.json"
        body = {**body, "name": name, "method": method, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        if dbg.exists():  # keep the extraction the review was made against next to it
            d = json.loads(dbg.read_text())
            body["extracted"] = [{"label": e["label"], "raw_text": e["raw_text"], "swatch": e["swatch"],
                                  "ignored": e["ignored"]} for e in d["entries"]]
        (out_dir / f"{safe(name)}.review.json").write_text(json.dumps(body, indent=1))
        return {"ok": True}

    @r.get("/{name}")
    def legend(name: str, method: str = Query("grid", pattern="^(old|grid)$"), force: bool = Query(False)):
        p, ann = find(name)
        if p is None:
            return err("unknown log", 404)
        f = out_dir / f"{safe(name)}.{method}.json"
        if f.exists() and not force and (out_dir / f"{safe(name)}.crop.png").exists():
            return json.loads(f.read_text())
        try:
            return compute(name, p, ann, method)
        except FileNotFoundError as e:
            return err(str(e), 404)
        except Exception as e:
            return err(f"{type(e).__name__}: {e}", 500)

    return r
