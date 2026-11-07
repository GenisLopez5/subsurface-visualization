"""Lithology Console: a local web app around lithology_pipeline.run_folder.

Start it with ./run.sh (or: .venv/bin/python -m uvicorn webapp.app:app --host 127.0.0.1 --port 8000).

Layout on disk (everything stays inside runs/):
    runs/<YYYYmmdd-HHMMSS>/
        meta.json        name, status, mode, config, digest of the results
        data/            uploaded files, or symlinks to a folder on this computer ("link a folder")
        data/labels/     ground-truth CSVs (example.csv stays in data/)
        work/            symlinks to the logs selected for this run (what the pipeline actually reads)
        job.json         what the worker runs;  events.jsonl  progress events;  result.json  worker outcome
        outputs/         pipeline outputs (or a symlink to an existing outputs folder for imported results)
        tiles/           the very tall output PNGs cut into 2048-px tiles for the browser viewer

The pipeline runs in a worker process (webapp/worker.py) supervised by a background thread: one job at a time.
"""
import asyncio
import dataclasses
import inspect
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from starlette.middleware.trustedhost import TrustedHostMiddleware

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import lithology_pipeline as lp  # noqa: E402

Image.MAX_IMAGE_PIXELS = None
RUNS = Path(os.environ.get("LITHO_RUNS_DIR", ROOT / "runs")).resolve()
RUNS.mkdir(parents=True, exist_ok=True)
STATIC = Path(__file__).parent / "static"
WORKER = Path(__file__).parent / "worker.py"
PRESETS = RUNS / ".presets.json"

RUN_ID = re.compile(r"^\d{8}-\d{6}(-\d+)?$")
IMAGE_EXT = set(lp.IMAGE_SUFFIXES)
STAGES = ["column", "segmentation", "legend+naming", "depth", "render"]
TERMINAL = {"done", "failed", "cancelled", "interrupted", "imported"}
TILE_H = 2048

app = FastAPI(title="Lithology Console", docs_url="/api/docs", redoc_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])
app.mount("/static", StaticFiles(directory=STATIC), name="static")

META_LOCK = threading.Lock()
JOB_LOCK = threading.Lock()
TILE_LOCK = threading.Lock()
JOB: dict = {}                       # the running job: run_id, pid, proc, cancel, rss_mb, peak_rss_mb


# ======================================================================================================================
# Helpers
# ======================================================================================================================
def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def run_path(rid: str) -> Path:
    if not RUN_ID.fullmatch(rid or ""):
        raise HTTPException(404, "unknown run")
    d = RUNS / rid
    if not (d / "meta.json").is_file():
        raise HTTPException(404, "unknown run")
    return d


def read_meta(d: Path) -> dict:
    return json.loads((d / "meta.json").read_text())


def write_meta(d: Path, **changes) -> dict:
    with META_LOCK:
        meta = read_meta(d) if (d / "meta.json").is_file() else {}
        meta.update(changes)
        tmp = d / "meta.json.tmp"
        tmp.write_text(json.dumps(meta, indent=2, default=str))
        os.replace(tmp, d / "meta.json")
        return meta


def new_run(name: str = "", mode: str = "upload", **extra) -> Path:
    base = time.strftime("%Y%m%d-%H%M%S")
    rid, i = base, 2
    while (RUNS / rid).exists():
        rid, i = f"{base}-{i}", i + 1
    d = RUNS / rid
    (d / "data" / "labels").mkdir(parents=True)
    write_meta(d, id=rid, name=(name or "").strip()[:120], created=now(), mode=mode, status="draft", **extra)
    return d


def safe_name(raw: str) -> str:
    """Uploaded filename -> safe file name. Path separators, '..' and hidden/control names are rejected."""
    name = (raw or "").strip()
    if not name or "/" in name or "\\" in name or "\x00" in name or name.startswith(".") or len(name) > 255:
        raise ValueError(f"unsafe file name: {raw!r}")
    if any(not c.isprintable() for c in name):
        raise ValueError(f"unsafe file name: {raw!r}")
    return name


def place_symlink(dest: Path, target: Path) -> None:
    """(Re)create dest as a symlink to target. Never writes through an existing link."""
    if dest.is_symlink() or dest.exists():
        dest.unlink()
    dest.symlink_to(Path(os.path.realpath(target)))


def mem_available_mb() -> Optional[float]:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return round(int(line.split()[1]) / 1024, 0)
    except OSError:
        pass
    return None


def tree_rss_mb(pid: int) -> Optional[float]:
    """Resident memory of a process and its children (Tesseract), Linux /proc only."""
    try:
        pids, kids = {pid}, {}
        for p in Path("/proc").iterdir():
            if p.name.isdigit():
                try:
                    ppid = int((p / "stat").read_text().rsplit(")", 1)[1].split()[1])
                    kids.setdefault(ppid, []).append(int(p.name))
                except (OSError, IndexError, ValueError):
                    pass
        todo = [pid]
        while todo:
            for c in kids.get(todo.pop(), []):
                if c not in pids:
                    pids.add(c)
                    todo.append(c)
        total = 0
        for p in pids:
            try:
                for line in Path(f"/proc/{p}/status").read_text().splitlines():
                    if line.startswith("VmRSS:"):
                        total += int(line.split()[1])
            except OSError:
                pass
        return round(total / 1024, 0)
    except OSError:
        return None


def pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        return "worker.py" in Path(f"/proc/{pid}/cmdline").read_text()
    except OSError:
        return True


_TESS_VERSION: Optional[str] = None


def tesseract_info() -> dict:
    global _TESS_VERSION
    try:
        lp.check_tesseract()
    except RuntimeError as e:
        return dict(ok=False, message=str(e))
    if _TESS_VERSION is None:
        try:
            out = subprocess.run([lp.pytesseract.pytesseract.tesseract_cmd, "--version"], capture_output=True,
                                 text=True, timeout=10)
            _TESS_VERSION = (out.stdout or out.stderr).splitlines()[0]
        except Exception:
            _TESS_VERSION = "unknown version"
    return dict(ok=True, version=_TESS_VERSION, path=str(lp.pytesseract.pytesseract.tesseract_cmd))


# ======================================================================================================================
# Configuration schema (generated from lithology_pipeline.Config, comments included)
# ======================================================================================================================
def config_schema() -> list:
    defaults = lp.Config()
    names = {f.name for f in dataclasses.fields(lp.Config)}
    group, out = "General", []
    for line in inspect.getsource(lp.Config).splitlines():
        g = re.match(r"\s*#\s*-+\s*(.+?)\s*-+\s*$", line)
        if g:
            group = g.group(1).strip().capitalize()
            continue
        m = re.match(r"\s*(\w+)\s*:\s*[^=]+=\s*(.+?)(?:\s+#\s*(.*))?$", line)
        if not m or m.group(1) not in names:
            continue
        name = m.group(1)
        d = getattr(defaults, name)
        kind = "bool" if isinstance(d, bool) else "int" if isinstance(d, int) else "float" if isinstance(d, float) else "list"
        elem = None if kind != "list" else ("int" if all(isinstance(x, int) for x in d) else "float")
        out.append(dict(name=name, group=group, type=kind, elem=elem, default=list(d) if kind == "list" else d,
                        help=(m.group(3) or "").strip()))
    return out


def validate_config(overrides: Optional[dict]) -> dict:
    """User overrides -> complete config dict (JSON-friendly). Unknown keys and wrong types are rejected (400)."""
    full = dataclasses.asdict(lp.Config())
    for k, v in (overrides or {}).items():
        if k not in full:
            raise HTTPException(400, f"unknown setting: {k}")
        d = full[k]
        try:
            if isinstance(d, bool):
                if not isinstance(v, bool):
                    raise ValueError
                full[k] = v
            elif isinstance(d, int):
                if isinstance(v, bool) or float(v) != int(float(v)):
                    raise ValueError
                full[k] = int(float(v))
            elif isinstance(d, float):
                if isinstance(v, bool) or not math.isfinite(float(v)):
                    raise ValueError
                full[k] = float(v)
            else:                                                   # tuple
                if isinstance(v, str):
                    v = [x for x in re.split(r"[,\s]+", v.strip()) if x]
                cast = int if all(isinstance(x, int) for x in d) else float
                vals = [cast(float(x)) for x in v]
                if not vals or (len(d) == 2 and k.endswith("_range") and (len(vals) != 2 or vals[0] > vals[1])):
                    raise ValueError
                full[k] = vals
        except (TypeError, ValueError):
            raise HTTPException(400, f"invalid value for {k}: {v!r}")
    for k, v in full.items():
        if isinstance(v, tuple):
            full[k] = list(v)
    return full


BUILTIN_PRESETS = {
    "Default (tuned on the example log)": {},
    "Fixed scale (P = 25 px)": {"auto_scale": False},
    "Strict naming (5% tie ratio)": {"tie_ratio": 0.05},
    "Lenient naming (20% tie ratio)": {"tie_ratio": 0.20},
    "Faster (fewer swatch variants, 2 depth strips)": {"swatch_scales": [0.5, 0.8, 1.0, 1.25],
                                                       "swatch_dilations": [0, 1, 2], "depth_max_strips": 2},
    "Small previews": {"page_scale": 0.15, "column_scale": 0.35},
}


def load_presets() -> dict:
    user = {}
    if PRESETS.is_file():
        try:
            user = json.loads(PRESETS.read_text())
        except json.JSONDecodeError:
            user = {}
    return dict(builtin=BUILTIN_PRESETS, user=user)


# ======================================================================================================================
# Pre-flight
# ======================================================================================================================
def list_data(d: Path):
    data = d / "data"
    files = [p for p in data.iterdir() if (p.is_file() or p.is_symlink()) and not p.name.endswith(".part")]
    labels = [p for p in (data / "labels").iterdir() if p.suffix.lower() == ".csv"] if (data / "labels").is_dir() else []
    return files, labels


def check_labels(p: Path) -> Optional[str]:
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            head = f.readline().strip().lower().split(",")
    except OSError as e:
        return f"cannot read: {e}"
    missing = [c for c in ("lithology", "top_y", "bottom_y") if c not in head]
    return f"missing column(s): {', '.join(missing)}" if missing else None


def preflight(d: Path) -> dict:
    files, label_files = list_data(d)
    by_name = {p.name: p for p in files}
    jsons = sorted(p for p in files if p.suffix.lower() == ".json")
    images = [p for p in files if p.suffix.lower() in IMAGE_EXT]
    csvs = {p.stem: p for p in label_files}
    csvs.update({p.stem: p for p in files if p.suffix.lower() == ".csv" and p.name != "example.csv"})
    example = by_name.get("example.csv")
    rows, used = [], set()
    for jp in jsons:
        row = dict(stem=jp.stem, json=jp.name, errors=[], warnings=[], json_ok=False, image=None, labels=None)
        ann = None
        try:
            ann = json.loads(jp.read_text())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            row["errors"].append(f"invalid JSON: {e}")
        boxes = (ann or {}).get("bounding_boxes") if isinstance(ann, dict) else None
        if ann is not None:
            if not isinstance(boxes, dict):
                row["errors"].append("JSON has no 'bounding_boxes'")
            else:
                bad = [k for k, v in boxes.items() if not (isinstance(v, list) and len(v) == 4 and
                                                           all(isinstance(x, (int, float)) for x in v))]
                for k in ("interpreted_lithology_column", "lithology_legend"):
                    if k not in boxes:
                        row["errors"].append(f"missing box '{k}'")
                if "depth_column" not in boxes:
                    row["warnings"].append("no 'depth_column' box: depths will be empty")
                if bad:
                    row["errors"].append(f"malformed box(es): {', '.join(bad)}")
                row["json_ok"] = not row["errors"]
                row["boxes"] = sorted(boxes)
        # image: JSON's image_name first, then <stem>.<image ext>
        img = None
        if isinstance(ann, dict) and ann.get("image_name") in by_name:
            img = by_name[ann["image_name"]]
        if img is None:
            img = next((p for p in images if p.stem == jp.stem), None)
        if img is None:
            want = ann.get("image_name") if isinstance(ann, dict) and ann.get("image_name") else f"{jp.stem}.tif"
            row["errors"].append(f"image not found (expected '{want}')")
        else:
            used.add(img.name)
            info = dict(name=img.name, size_mb=None, width=None, height=None, mode=None)
            try:
                info["size_mb"] = round(img.stat().st_size / 1e6, 1)
                with Image.open(img) as im:
                    info.update(width=im.width, height=im.height, mode=im.mode)
            except Exception as e:
                row["errors"].append(f"image unreadable: {type(e).__name__}: {e}")
            row["image"] = info
            if boxes and info["width"]:
                for k, b in boxes.items():
                    if isinstance(b, list) and len(b) == 4 and (b[2] > info["width"] + 2 or b[3] > info["height"] + 2):
                        row["warnings"].append(f"box '{k}' extends outside the image")
        # labels
        lab = csvs.get(jp.stem)
        if lab is None and example is not None and jp.stem == lp.EXAMPLE_STEM:
            lab = example
        if lab is not None:
            used.add(lab.name)
            err = check_labels(lab)
            row["labels"] = dict(name=lab.name, rel=str(lab.relative_to(d / "data")), ok=err is None, error=err)
            if err:
                row["warnings"].append(f"labels ignored: {err}")
        row["ok"] = not row["errors"]
        rows.append(row)
    stems = {r["stem"] for r in rows}
    unmatched = []
    for p in images:
        if p.name not in used:
            unmatched.append(dict(name=p.name, problem="image without a matching .json annotation"))
    for stem, p in csvs.items():
        if stem not in stems:
            unmatched.append(dict(name=p.name, problem="labels CSV without a matching log"))
    if example is not None and lp.EXAMPLE_STEM not in stems:
        unmatched.append(dict(name="example.csv", problem=f"ground truth for {lp.EXAMPLE_STEM}, which is not in this run"))
    for p in files:
        if p.suffix.lower() not in IMAGE_EXT | {".json", ".csv"}:
            unmatched.append(dict(name=p.name, problem="unsupported file type"))
    return dict(rows=rows, unmatched=unmatched, n_ok=sum(r["ok"] for r in rows), n_logs=len(rows))


# ======================================================================================================================
# Progress (from events.jsonl)
# ======================================================================================================================
def read_events(d: Path) -> list:
    p = d / "events.jsonl"
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def progress(d: Path, events: Optional[list] = None) -> dict:
    events = read_events(d) if events is None else events
    total, logs, current, t0, t_end, fatal = 0, {}, None, None, None, None
    for e in events:
        ev = e.get("event")
        t0 = t0 or e.get("t")
        if ev == "start":
            total = e["n_logs"]
        elif ev == "log_start":
            current = e["log"]
            logs[e["log"]] = dict(stem=e["log"], index=e["index"], status="running", stages={}, t_start=e.get("t"))
        elif ev == "stage" and e["log"] in logs:
            logs[e["log"]]["stages"][e["stage"]] = e["seconds"]
        elif ev == "log_done" and e["log"] in logs:
            logs[e["log"]].update(status="done", n_intervals=e["n_intervals"], runtime_s=e["runtime_s"],
                                  metrics=e.get("metrics"), ambiguous=e.get("ambiguous", []), t_end=e.get("t"))
        elif ev == "log_error" and e["log"] in logs:
            logs[e["log"]].update(status="error", error=e["error"], t_end=e.get("t"))
        elif ev == "done":
            t_end = e.get("t")
        elif ev == "fatal":
            fatal, t_end = e["error"], e.get("t")
    finished = sum(1 for r in logs.values() if r["status"] != "running")
    cur = logs.get(current) if current and logs.get(current, {}).get("status") == "running" else None
    frac_cur = len(cur["stages"]) / len(STAGES) if cur else 0.0
    fraction = min(1.0, (finished + frac_cur) / total) if total else 0.0
    meta = read_meta(d)
    if t_end is None and meta.get("status") in TERMINAL and events:
        t_end = events[-1].get("t")
    elapsed = ((t_end or time.time()) - t0) if t0 else 0.0
    eta = elapsed * (1 - fraction) / fraction if 0.02 < fraction < 1 and t_end is None else None
    next_stage = STAGES[len(cur["stages"])] if cur and len(cur["stages"]) < len(STAGES) else None
    return dict(total=total, finished=finished, n_errors=sum(1 for r in logs.values() if r["status"] == "error"),
                fraction=round(fraction, 4), elapsed_s=round(elapsed, 1), eta_s=None if eta is None else round(eta, 0),
                current=dict(stem=cur["stem"], index=cur["index"], stages=cur["stages"], stage=next_stage,
                             running_s=round(time.time() - cur["t_start"], 1) if cur.get("t_start") else None)
                if cur else None,
                logs=sorted(logs.values(), key=lambda r: r["index"]), fatal=fatal, n_events=len(events))


def status_payload(d: Path) -> dict:
    meta = read_meta(d)
    out = dict(meta=meta, progress=progress(d), mem_available_mb=mem_available_mb())
    if JOB.get("run_id") == meta["id"]:
        out["worker"] = dict(pid=JOB.get("pid"), rss_mb=JOB.get("rss_mb"), peak_rss_mb=JOB.get("peak_rss_mb"),
                             cancelling=JOB.get("cancel", False))
    return out


# ======================================================================================================================
# Job control
# ======================================================================================================================
def _supervise(rid: str, proc: Optional[subprocess.Popen], pid: int) -> None:
    d = RUNS / rid
    while (proc.poll() is None) if proc else pid_alive(pid):
        rss = tree_rss_mb(pid)
        if rss is not None:
            JOB["rss_mb"] = rss
            JOB["peak_rss_mb"] = max(JOB.get("peak_rss_mb") or 0, rss)
        time.sleep(1)
    code = proc.returncode if proc else None
    result = None
    if (d / "result.json").is_file():
        try:
            result = json.loads((d / "result.json").read_text())
        except json.JSONDecodeError:
            pass
    if JOB.get("cancel"):
        status, error = "cancelled", "cancelled by user"
    elif result:
        status, error = result["status"], result.get("error")
    elif code is not None and code < 0 and -code == signal.SIGKILL:
        status, error = "failed", "the worker was killed (most likely out of memory)"
    else:
        status, error = "failed", f"the worker stopped unexpectedly (exit code {code}); see worker.log"
    digest = digest_from_summary(d)
    write_meta(d, status=status, finished=now(), error=error, peak_rss_mb=JOB.get("peak_rss_mb"), **digest)
    with JOB_LOCK:
        JOB.clear()


def start_worker(d: Path) -> None:
    rid = d.name
    log = open(d / "worker.log", "ab")
    proc = subprocess.Popen([sys.executable, str(WORKER), str(d)], cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)
    JOB.update(run_id=rid, pid=proc.pid, proc=proc, cancel=False, rss_mb=None, peak_rss_mb=None)
    write_meta(d, status="running", started=now(), pid=proc.pid, finished=None, error=None)
    threading.Thread(target=_supervise, args=(rid, proc, proc.pid), daemon=True).start()


def recover_runs() -> None:
    """On startup: re-attach to a worker that survived a server restart; mark dead 'running' runs as interrupted."""
    for d in RUNS.iterdir():
        if not (d / "meta.json").is_file():
            continue
        try:
            meta = read_meta(d)
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("status") != "running":
            continue
        if pid_alive(meta.get("pid")) and not JOB:
            JOB.update(run_id=meta["id"], pid=meta["pid"], proc=None, cancel=False, rss_mb=None, peak_rss_mb=None)
            threading.Thread(target=_supervise, args=(meta["id"], None, meta["pid"]), daemon=True).start()
        else:
            write_meta(d, status="interrupted", error="the server stopped while this run was in progress",
                       **digest_from_summary(d))


def digest_from_summary(d: Path) -> dict:
    s = d / "outputs" / "summary.json"
    if s.is_file():
        try:
            j = json.loads(s.read_text())
            return dict(n_logs=j.get("n_logs"), n_errors=j.get("n_errors"), total_s=j.get("total_s"),
                        mean_metrics=j.get("mean_metrics"), has_summary=True)
        except (OSError, json.JSONDecodeError):
            pass
    p = progress(d)
    return dict(n_logs=p["total"] or None, n_errors=p["n_errors"], total_s=p["elapsed_s"], has_summary=False)


recover_runs()


# ======================================================================================================================
# API: health, config, presets, suggestions, how it works
# ======================================================================================================================
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return RedirectResponse("/static/favicon.svg")


@app.get("/api/health")
def health():
    return dict(tesseract=tesseract_info(), python=sys.version.split()[0], pipeline=str(ROOT / "lithology_pipeline.py"),
                runs_dir=str(RUNS), busy=JOB.get("run_id"), mem_available_mb=mem_available_mb(), cpus=os.cpu_count())


@app.get("/api/config")
def get_config():
    return dict(fields=config_schema(), presets=load_presets())


@app.post("/api/presets")
def save_preset(body: dict = Body(...)):
    name = str(body.get("name", "")).strip()[:80]
    if not name or name in BUILTIN_PRESETS:
        raise HTTPException(400, "choose a new preset name")
    cfg = validate_config(body.get("config") or {})
    defaults = validate_config({})
    user = load_presets()["user"]
    user[name] = {k: v for k, v in cfg.items() if v != defaults[k]}
    PRESETS.write_text(json.dumps(user, indent=2))
    return load_presets()


@app.delete("/api/presets/{name}")
def delete_preset(name: str):
    user = load_presets()["user"]
    user.pop(name, None)
    PRESETS.write_text(json.dumps(user, indent=2))
    return load_presets()


@app.get("/api/how")
def how_it_works():
    doc = lp.__doc__ or ""
    block = doc.split("How it works", 1)[-1].split("Usage", 1)[0]
    steps, cur = [], None
    for line in block.splitlines():
        m = re.match(r"\s*(\d+)\.\s+(\w[\w+ ]*?)\s{2,}(.*)", line)
        if m:
            cur = dict(n=int(m.group(1)), title=m.group(2).strip(), text=m.group(3).strip())
            steps.append(cur)
        elif cur and line.strip() and not set(line.strip()) <= {"-"}:
            cur["text"] += " " + line.strip()
    head = re.search(r"\((.*?)\)", block)
    return dict(steps=steps, note=head.group(1) if head else "")


def _looks_like_data(p: Path) -> bool:
    if (p / "bbox_annotation").is_dir():
        return True
    try:
        jsons = [q.stem for q in list(p.glob("*.json"))[:50]]
        return any(any((p / f"{s}{e}").exists() for e in lp.IMAGE_SUFFIXES) for s in jsons)
    except OSError:
        return False


@app.get("/api/suggestions")
def suggestions():
    """Folders on this computer that look like log data or pipeline results (to pre-fill the forms)."""
    skip = {".venv", ".git", "node_modules", "__pycache__", "runs", "tiles", "bbox_vis", "images", "bbox_annotation"}
    data, results, labels = [], [], []
    for base, depth in ((ROOT.parent.parent, 3),):
        stack = [(base, 0)]
        while stack:
            p, k = stack.pop()
            try:
                subs = sorted(q for q in p.iterdir() if q.is_dir() and q.name not in skip and not q.name.startswith("."))
            except OSError:
                continue
            if (p / "summary.json").is_file():
                results.append(str(p))
            elif _looks_like_data(p):
                data.append(str(p))
            if p.name == "labels" and any(p.glob("*.csv")):
                labels.append(str(p))
            if k < depth:
                stack += [(q, k + 1) for q in subs]
    return dict(data=sorted(data), results=sorted(results), labels=sorted(labels))


# ======================================================================================================================
# API: runs
# ======================================================================================================================
@app.get("/api/runs")
def list_runs():
    out = []
    for d in sorted(RUNS.iterdir(), reverse=True):
        if d.is_dir() and RUN_ID.fullmatch(d.name) and (d / "meta.json").is_file():
            try:
                m = read_meta(d)
            except (OSError, json.JSONDecodeError):
                continue
            out.append({k: m.get(k) for k in ("id", "name", "created", "mode", "status", "n_logs", "n_errors",
                                              "total_s", "mean_metrics", "source", "started", "finished", "error")})
    return dict(runs=out, busy=JOB.get("run_id"))


@app.post("/api/runs")
def create_run(body: Optional[dict] = Body(None)):
    d = new_run((body or {}).get("name", ""))
    return read_meta(d)


@app.get("/api/runs/{rid}")
def get_run(rid: str):
    return status_payload(run_path(rid))


@app.patch("/api/runs/{rid}")
def rename_run(rid: str, body: dict = Body(...)):
    return write_meta(run_path(rid), name=str(body.get("name", "")).strip()[:120])


@app.delete("/api/runs/{rid}")
def delete_run(rid: str):
    d = run_path(rid)
    if JOB.get("run_id") == rid:
        raise HTTPException(409, "this run is in progress: cancel it first")
    for root, dirs, files in os.walk(d, followlinks=False):           # unlink symlinks first: never touch targets
        for n in dirs + files:
            p = Path(root) / n
            if p.is_symlink():
                p.unlink()
    shutil.rmtree(d)
    return dict(deleted=rid)


def require_draft(d: Path) -> dict:
    meta = read_meta(d)
    if meta["status"] != "draft":
        raise HTTPException(409, "files can only be changed before the run starts (use 'Clone' to re-run)")
    return meta


@app.post("/api/runs/{rid}/files")
def upload_files(rid: str, files: list[UploadFile] = File(...)):
    """Multipart upload, streamed to disk in 1 MB chunks. Images + JSON -> data/, CSV -> data/labels/
    (example.csv -> data/)."""
    d = run_path(rid)
    require_draft(d)
    saved, rejected = [], []
    for f in files:
        try:
            name = safe_name(f.filename)
        except ValueError as e:
            rejected.append(dict(name=f.filename, error=str(e)))
            continue
        ext = Path(name).suffix.lower()
        if ext == ".json" or ext in IMAGE_EXT or name.lower() == "example.csv":
            dest = d / "data" / name
        elif ext == ".csv":
            dest = d / "data" / "labels" / name
        else:
            rejected.append(dict(name=name, error="unsupported file type (expected an image, .json or .csv)"))
            continue
        tmp = dest.with_name(dest.name + ".part")
        with open(tmp, "wb") as out:
            shutil.copyfileobj(f.file, out, 1 << 20)
        os.replace(tmp, dest)                                           # replaces a symlink, never writes through it
        saved.append(dict(name=name, size_mb=round(dest.stat().st_size / 1e6, 2), to=str(dest.relative_to(d))))
    if saved:
        meta = read_meta(d)
        write_meta(d, uploaded=True, mode="mixed" if meta.get("sources") else "upload")
    return dict(saved=saved, rejected=rejected, preflight=preflight(d))


@app.delete("/api/runs/{rid}/logs/{stem}")
def remove_log(rid: str, stem: str):
    """Remove one log (its .json, image and labels) from a draft run."""
    d = run_path(rid)
    require_draft(d)
    pf = preflight(d)
    row = next((r for r in pf["rows"] if r["stem"] == stem), None)
    if row is None:
        raise HTTPException(404, "unknown log")
    names = [row["json"]] + ([row["image"]["name"]] if row["image"] else [])
    for n in names:
        (d / "data" / n).unlink(missing_ok=True)
    if row["labels"] and row["labels"]["name"] != "example.csv":
        (d / "data" / row["labels"]["rel"]).unlink(missing_ok=True)
    return preflight(d)


@app.delete("/api/runs/{rid}/data/{name}")
def remove_file(rid: str, name: str):
    d = run_path(rid)
    require_draft(d)
    try:
        name = safe_name(name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    for p in (d / "data" / name, d / "data" / "labels" / name):
        if p.is_file() or p.is_symlink():
            p.unlink()
    return preflight(d)


@app.post("/api/runs/{rid}/link")
def link_folder(rid: str, body: dict = Body(...)):
    """Add every log of a folder on this computer *without copying*: symlinks in data/ point to the originals.
    Accepts a flat folder (<stem>.json + image) or the organisers' layout (bbox_annotation/ + images/)."""
    d = run_path(rid)
    require_draft(d)
    src = Path(os.path.expanduser(str(body.get("path", "")).strip()))
    if not src.is_absolute() or not src.is_dir():
        raise HTTPException(400, f"not a folder: {src}")
    lab_raw = str(body.get("labels") or "").strip()
    lab = Path(os.path.expanduser(lab_raw)) if lab_raw else None
    if lab is not None and not lab.is_dir():
        raise HTTPException(400, f"labels folder not found: {lab}")
    logs = lp.find_logs(src, lab)
    if not logs:
        raise HTTPException(400, "no logs found: need <stem>.json with bounding_boxes + its image "
                                 "(flat, or bbox_annotation/ + images/)")
    for log in logs:
        place_symlink(d / "data" / log.annotation_path.name, log.annotation_path)
        place_symlink(d / "data" / log.image_path.name, log.image_path)
        if log.labels_path:
            place_symlink(d / "data" / "labels" / f"{log.stem}.csv", log.labels_path)
    ex = next((p for p in (src / "example.csv", src / "example" / "example.csv") if p.is_file()), None)
    if ex and any(l.stem == lp.EXAMPLE_STEM and l.labels_path is None for l in logs):
        place_symlink(d / "data" / "example.csv", ex)
    meta = read_meta(d)
    sources = list(dict.fromkeys((meta.get("sources") or []) + [str(src)]))
    write_meta(d, mode="mixed" if meta.get("uploaded") else "folder", sources=sources, source=", ".join(sources))
    return dict(linked=len(logs), labelled=sum(1 for l in logs if l.labels_path), preflight=preflight(d))


@app.post("/api/runs/import")
def import_results(body: dict = Body(...)):
    """Open an existing outputs folder (with summary.json), e.g. one written by the CLI. Read-only: it is linked."""
    src = Path(os.path.expanduser(str(body.get("path", "")).strip()))
    if not src.is_absolute() or not (src / "summary.json").is_file():
        raise HTTPException(400, f"no summary.json in {src}")
    d = new_run(body.get("name") or f"Imported: {src.name}", mode="import", source=str(src))
    (d / "outputs").symlink_to(src.resolve())
    write_meta(d, status="imported", finished=now(), **digest_from_summary(d))
    return read_meta(d)


@app.post("/api/runs/{rid}/clone")
def clone_run(rid: str, body: Optional[dict] = Body(None)):
    """New draft run with the same input files (linked, not copied) and the same settings: tweak and re-run."""
    d = run_path(rid)
    meta = read_meta(d)
    if not (d / "data").is_dir() or meta.get("mode") == "import":
        raise HTTPException(400, "this run has no input files to clone (imported results)")
    nd = new_run((body or {}).get("name") or f"{meta.get('name') or rid} (copy)", mode="folder",
                 config_overrides=meta.get("config_overrides") or {}, cloned_from=rid,
                 source=meta.get("source"), sources=meta.get("sources"))
    files, labels = list_data(d)
    for p in files:
        place_symlink(nd / "data" / p.name, p)
    for p in labels:
        place_symlink(nd / "data" / "labels" / p.name, p)
    return read_meta(nd)


@app.get("/api/runs/{rid}/preflight")
def get_preflight(rid: str):
    d = run_path(rid)
    if not (d / "data").is_dir():
        return dict(rows=[], unmatched=[], n_ok=0, n_logs=0)
    return preflight(d)


@app.post("/api/runs/{rid}/start")
def start_run(rid: str, body: Optional[dict] = Body(None)):
    d = run_path(rid)
    body = body or {}
    with JOB_LOCK:
        if JOB:
            raise HTTPException(409, f"another run is in progress ({JOB.get('run_id')}); one job at a time")
        meta = require_draft(d)
        overrides = body.get("config") or {}
        cfg = validate_config(overrides)
        pf = preflight(d)
        ok = [r for r in pf["rows"] if r["ok"]]
        want = body.get("logs")
        sel = [r for r in ok if want is None or r["stem"] in set(want)]
        if not sel:
            raise HTTPException(400, "no valid log selected (see the pre-flight table)")
        work = d / "work"
        (work / "labels").mkdir(parents=True, exist_ok=True)
        for r in sel:                                                   # only the selected logs, as symlinks
            place_symlink(work / r["json"], d / "data" / r["json"])
            place_symlink(work / r["image"]["name"], d / "data" / r["image"]["name"])
            if r["labels"] and r["labels"]["ok"]:
                place_symlink(work / "labels" / f"{r['stem']}.csv", d / "data" / r["labels"]["rel"])
        (d / "job.json").write_text(json.dumps(dict(data_dir=str(work), out_dir=str(d / "outputs"),
                                                    labels_dir=str(work / "labels"), config=cfg), indent=2))
        defaults = validate_config({})
        write_meta(d, config=cfg, config_overrides={k: v for k, v in cfg.items() if v != defaults[k]},
                   selected=[r["stem"] for r in sel], n_logs=len(sel), n_labelled=sum(1 for r in sel if r["labels"]))
        for f in ("events.jsonl", "result.json"):
            (d / f).unlink(missing_ok=True)
        start_worker(d)
    return status_payload(d)


@app.post("/api/runs/{rid}/cancel")
def cancel_run(rid: str):
    d = run_path(rid)
    if JOB.get("run_id") != rid:
        raise HTTPException(409, "this run is not in progress")
    JOB["cancel"] = True
    pid = JOB.get("pid")

    def stop():
        for sig, wait in ((signal.SIGTERM, 5), (signal.SIGKILL, 0)):
            try:
                os.killpg(pid, sig)
            except (ProcessLookupError, PermissionError):
                return
            t = time.time()
            while time.time() - t < wait and pid_alive(pid):
                time.sleep(0.2)
            if not pid_alive(pid):
                return
    threading.Thread(target=stop, daemon=True).start()
    return status_payload(d)


@app.get("/api/runs/{rid}/status")
def get_status(rid: str):
    return status_payload(run_path(rid))


@app.get("/api/runs/{rid}/events")
async def stream_events(rid: str, request: Request, since: int = 0):
    """Server-Sent Events: every pipeline event (id = line number, so the browser resumes after a reconnect) plus a
    'status' event every second with progress and memory. Ends with an 'end' event when the run is over."""
    d = run_path(rid)
    try:
        since = max(since, int(request.headers.get("last-event-id", 0)))
    except ValueError:
        pass

    async def gen():
        path, pos, offset, buf = d / "events.jsonl", 0, 0, b""
        yield "retry: 2000\n\n"
        while True:
            if await request.is_disconnected():
                break
            if path.is_file():
                with open(path, "rb") as f:
                    f.seek(offset)
                    chunk = f.read()
                offset += len(chunk)
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for line in lines:
                    pos += 1
                    if pos > since and line.strip():
                        yield f"id: {pos}\ndata: {line.decode('utf-8', 'replace')}\n\n"
            st = status_payload(d)
            p = st["progress"]
            yield "event: status\ndata: " + json.dumps(dict(
                status=st["meta"]["status"], fraction=p["fraction"], finished=p["finished"], total=p["total"],
                elapsed_s=p["elapsed_s"], eta_s=p["eta_s"], current=p["current"], n_errors=p["n_errors"],
                worker=st.get("worker"), mem_available_mb=st["mem_available_mb"])) + "\n\n"
            if st["meta"]["status"] in TERMINAL or st["meta"]["status"] == "draft":
                yield "event: end\ndata: " + json.dumps(dict(status=st["meta"]["status"])) + "\n\n"
                break
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/runs/{rid}/summary")
def get_summary(rid: str):
    """summary.json when the run finished; otherwise a partial summary built from the events (live results)."""
    d = run_path(rid)
    s = d / "outputs" / "summary.json"
    if s.is_file():
        j = json.loads(s.read_text())
        j["partial"] = False
        return j
    p = progress(d)
    logs = [dict(stem=r["stem"], n_intervals=r.get("n_intervals"), runtime_s=r.get("runtime_s"), metrics=r.get("metrics"),
                 ambiguous=r.get("ambiguous", []), timings_s=r["stages"], partial=True)
            for r in p["logs"] if r["status"] == "done"]
    errors = [dict(log=r["stem"], error=r.get("error")) for r in p["logs"] if r["status"] == "error"]
    meta = read_meta(d)
    return dict(partial=True, n_logs=p["total"], n_errors=len(errors), total_s=p["elapsed_s"], errors=errors,
                logs=logs, mean_metrics=None, config=meta.get("config") or dataclasses.asdict(lp.Config()))


_ANN_CACHE: dict = {}


def annotation_for(d: Path, stem: str, summary: Optional[dict]) -> Optional[dict]:
    """Bounding boxes of a log: from the run's own files, or (imported results) from the summary's data_dir."""
    for p in (d / "work" / f"{stem}.json", d / "data" / f"{stem}.json"):
        if p.is_file():
            try:
                return json.loads(p.read_text()).get("bounding_boxes")
            except (OSError, json.JSONDecodeError):
                return None
    data_dir = (summary or {}).get("data_dir")
    if data_dir and Path(data_dir).is_dir():
        if data_dir not in _ANN_CACHE:
            try:
                _ANN_CACHE[data_dir] = {l.stem: l.annotation_path for l in lp.find_logs(Path(data_dir))}
            except OSError:
                _ANN_CACHE[data_dir] = {}
        p = _ANN_CACHE[data_dir].get(stem)
        if p:
            try:
                return json.loads(Path(p).read_text()).get("bounding_boxes")
            except (OSError, json.JSONDecodeError):
                return None
    return None


@app.get("/api/runs/{rid}/logs/{stem}")
def log_detail(rid: str, stem: str):
    d = run_path(rid)
    out_dir = d / "outputs"
    summary = None
    if (out_dir / "summary.json").is_file():
        summary = json.loads((out_dir / "summary.json").read_text())
    record = next((r for r in (summary or {}).get("logs", []) if r["stem"] == stem), None)
    error = next((e["error"] for e in (summary or {}).get("errors", []) if e["log"] == stem), None)
    files = {}
    for key, suffix in (("csv", ".csv"), ("page_png", "_page.png"), ("column_png", "_column.png"),
                        ("legend_png", "_legend.png"), ("depth_png", "_depth.png")):
        if (out_dir / f"{stem}{suffix}").is_file():
            files[key] = f"{stem}{suffix}"
    if not files and record is None and error is None:
        p = next((r for r in progress(d)["logs"] if r["stem"] == stem), None)
        if p is None:
            raise HTTPException(404, "unknown log")
        error = p.get("error")
    rows = None
    if record is None and "csv" in files:                              # finished during a run still in progress
        import csv
        with open(out_dir / files["csv"], newline="") as f:
            rows = [dict(top_depth=float(r["top_depth"]) if r["top_depth"] else None,
                         bottom_depth=float(r["bottom_depth"]) if r["bottom_depth"] else None,
                         lithology=r["lithology"], top_y=int(float(r["top_y"])), bottom_y=int(float(r["bottom_y"])),
                         cluster_id=int(float(r["cluster_id"])), ambiguous=False) for r in csv.DictReader(f)]
    cfg = (summary or {}).get("config") or read_meta(d).get("config") or dataclasses.asdict(lp.Config())
    sizes = {}
    for key in ("page_png", "column_png"):
        if key in files:
            try:
                with Image.open(out_dir / files[key]) as im:
                    sizes[key] = [im.width, im.height]
            except Exception:
                pass
    order = [r["stem"] for r in (summary or {}).get("logs", [])] + [e["log"] for e in (summary or {}).get("errors", [])]
    if not order:
        order = [r["stem"] for r in progress(d)["logs"]]
    return dict(stem=stem, record=record, error=error, files=files, rows=rows, boxes=annotation_for(d, stem, summary),
                page_scale=cfg.get("page_scale", 0.25), column_scale=cfg.get("column_scale", 0.5), sizes=sizes,
                order=order)


def _inside(d: Path, rel: str, roots=("outputs", "tiles")) -> Path:
    parts = Path(rel).parts
    if (not parts or Path(rel).is_absolute() or any(p in ("..", ".", "") for p in parts) or "\x00" in rel
            or "\\" in rel or parts[0] not in roots):
        raise HTTPException(400, "invalid path")
    p = d / rel
    if not p.is_file():
        raise HTTPException(404, "file not found")
    return p


@app.get("/api/runs/{rid}/files/{rel:path}")
def get_file(rid: str, rel: str, download: bool = False):
    """Serve a file of a run: only from its outputs/ and tiles/ folders."""
    d = run_path(rid)
    p = _inside(d, rel)
    return FileResponse(p, filename=p.name if download else None,
                        headers={"Cache-Control": "no-cache"} if p.suffix == ".json" else None)


@app.get("/api/runs/{rid}/tiles/{name}")
def get_tiles(rid: str, name: str):
    """Cut a (very tall) output PNG into 2048-px tiles + a 64-px-wide overview strip, once, and return the manifest.
    The browser then only loads the tiles in view."""
    d = run_path(rid)
    try:
        name = safe_name(name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not name.endswith(".png"):
        raise HTTPException(400, "only PNG outputs can be tiled")
    src = _inside(d, f"outputs/{name}")
    tdir = d / "tiles" / name[:-4]
    man = tdir / "manifest.json"
    with TILE_LOCK:
        if man.is_file() and json.loads(man.read_text()).get("mtime") == src.stat().st_mtime:
            return json.loads(man.read_text())
        if tdir.exists():
            shutil.rmtree(tdir)
        tdir.mkdir(parents=True)
        with Image.open(src) as im:
            im = im.convert("RGB")
            W, H = im.size
            n = max(1, math.ceil(H / TILE_H))
            for i in range(n):
                im.crop((0, i * TILE_H, W, min(H, (i + 1) * TILE_H))).save(tdir / f"{i:04d}.png", compress_level=1)
            th = max(1, min(H, 4000))
            im.resize((64, th), Image.Resampling.BOX).save(tdir / "overview.png")
        rel = f"tiles/{name[:-4]}"
        m = dict(width=W, height=H, tile_h=TILE_H, n=n, mtime=src.stat().st_mtime,
                 tiles=[f"{rel}/{i:04d}.png" for i in range(n)], overview=f"{rel}/overview.png")
        man.write_text(json.dumps(m))
        return m


@app.get("/api/runs/{rid}/download")
def download(rid: str, what: str = "csv"):
    """Zip of the results: 'csv' = every <stem>.csv + summary.json; 'all' = also every PNG."""
    d = run_path(rid)
    out = d / "outputs"
    if not out.is_dir():
        raise HTTPException(404, "no outputs yet")
    files = sorted(p for p in out.iterdir() if p.is_file() and (p.suffix in (".csv", ".json") or what == "all"))
    if not files:
        raise HTTPException(404, "no outputs yet")
    z = d / f"export_{what}.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, p.name, compress_type=zipfile.ZIP_STORED if p.suffix == ".png" else zipfile.ZIP_DEFLATED)
    meta = read_meta(d)
    label = re.sub(r"[^A-Za-z0-9._-]+", "_", meta.get("name") or rid).strip("_") or rid
    return FileResponse(z, filename=f"lithology_{label}_{what}.zip", media_type="application/zip")
