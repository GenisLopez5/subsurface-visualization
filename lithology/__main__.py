"""
Command line.

    python -m lithology serve   [--annotations DIR ...] [--images DIR ...] [--data-dir DIR]
                                [--ui DIR] [--split FILE] [--desktop] [--host H] [--port P]
    python -m lithology process IMAGE [--annotation JSON] [--annotations DIR ...] [-o OUT_DIR]
    python -m lithology extract IMAGE ANNOTATION.json [-o out.json] [--csv out.csv] [-v]
    python -m lithology batch   FOLDER [--redo] [--data-dir DIR]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Settings


def _settings(a) -> Settings:
    s = Settings.from_env()
    if getattr(a, "annotations", None):
        s.annotation_dirs = [Path(p).resolve() for p in a.annotations]
    if getattr(a, "images", None):
        s.image_dirs = [Path(p).resolve() for p in a.images]
    if getattr(a, "data_dir", None):
        s.data_dir = Path(a.data_dir).resolve()
    if getattr(a, "ui", None):
        s.ui_dir = Path(a.ui).resolve()
    if getattr(a, "split", None):
        s.split_file = Path(a.split).resolve()
    if getattr(a, "desktop", False):
        s.desktop = True
    s.ensure_dirs()
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m lithology", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="run the HTTP API")
    p.add_argument("--annotations", nargs="+", help="folders with existing annotation JSONs")
    p.add_argument("--images", nargs="+", help="folders with log images (to pair with an uploaded JSON)")
    p.add_argument("--data-dir", help="working folder (uploads, cache, temp files)")
    p.add_argument("--ui", help="folder with a web UI (index.html) to serve at /")
    p.add_argument("--split", help="dataset split.json (tags logs train/test in /api/dataset)")
    p.add_argument("--desktop", action="store_true", help="enable folder dialog / open-file endpoints")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)

    p = sub.add_parser("process", help="one image -> CSV + annotated TIF (no server)")
    p.add_argument("image")
    p.add_argument("--annotation", help="bounding-box JSON (default: look it up in --annotations)")
    p.add_argument("--boxes", choices=["dataset", "detector"],
                   help="without --annotation: the dataset JSON (default, LITHO_BOX_SOURCE) or automatic detection")
    p.add_argument("--annotations", nargs="+", help="folders with existing annotation JSONs")
    p.add_argument("-o", "--out", default=".", help="output folder")
    p.add_argument("--data-dir")
    p.add_argument("--debug", action="store_true", help="also write the full result JSON")

    sub.add_parser("extract", help="low level: image + annotation -> intervals JSON", add_help=False)

    p = sub.add_parser("batch", help="every image/JSON pair of a folder -> FOLDER/results")
    p.add_argument("folder")
    p.add_argument("--redo", action="store_true", help="process logs that already have results again")
    p.add_argument("--data-dir")

    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["extract"]:  # pass everything through to extract.py's own parser
        from .extract import main as extract_main
        return extract_main(argv[1:])
    a = ap.parse_args(argv)

    if a.cmd == "serve":
        import uvicorn

        from .api import create_app
        s = _settings(a)
        print(f"annotations: {[str(d) for d in s.annotation_dirs] or 'none'}\nAPI docs: http://{a.host}:{a.port}/docs")
        uvicorn.run(create_app(s), host=a.host, port=a.port, log_level="warning")

    elif a.cmd == "process":
        from . import pipeline
        from .annotations import AnnotationStore, load_detector, resolve_annotation
        s = _settings(a)
        img = Path(a.image)
        out = Path(a.out)
        if a.annotation:
            ann = Path(a.annotation)
        else:
            out.mkdir(parents=True, exist_ok=True)
            source = a.boxes or s.box_source
            ann, how = resolve_annotation(img, AnnotationStore(s.annotation_dirs),
                                          load_detector(s.detector) if source == "detector" else None, out, source=source)
            print(f"bounding boxes: {ann.name}" + (f" ({how})" if how else ""))
        r = pipeline.process(img, ann, out, img.stem, s.cache, s.tmp, log=print,
                             debug_dir=out if a.debug else None)
        print(f"{r['intervals']} intervals -> " + ", ".join(r["files"].values()))

    elif a.cmd == "batch":
        from .batch import run_batch, scan_folder
        s = _settings(a)
        scan = scan_folder(a.folder)
        print(f"JSON: {scan['json_folder']}\nimages: {scan['image_folder']}\n{len(scan['pairs'])} pairs")
        for m in scan["json_without_image"]:
            print("  no image for", m)
        rows = run_batch(scan, s.cache, s.tmp, force=a.redo)
        n = lambda st: sum(r["status"] == st for r in rows)  # noqa: E731
        print(f"{n('done')} done, {n('skipped')} skipped, {n('error')} errors -> {scan['results_folder']}")


if __name__ == "__main__":
    main()
