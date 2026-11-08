"""Background worker: runs lithology_pipeline.run_folder for one run folder, in its own process.

    python webapp/worker.py runs/<run_id>

Reads <run>/job.json, appends every on_progress event as one JSON line to <run>/events.jsonl and writes
<run>/result.json when it ends. Running in a separate process (instead of a thread inside the server) means the web
app can cancel a run (kill the process group, Tesseract included) and survives a log that runs out of memory.
The pipeline is called exactly as the CLI calls it; nothing in its algorithm is changed.
"""
import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main(run_dir: str) -> int:
    run = Path(run_dir)
    job = json.loads((run / "job.json").read_text())
    events = open(run / "events.jsonl", "a", buffering=1, encoding="utf-8")

    def emit(e: dict) -> None:
        events.write(json.dumps(dict(e, t=round(time.time(), 3)), default=str) + "\n")
        events.flush()

    emit(dict(event="worker_start", pid=os.getpid()))
    try:
        import lithology_pipeline as lp
        cfg = lp.Config(**{k: tuple(v) if isinstance(v, list) else v for k, v in job["config"].items()})
        s = lp.run_folder(job["data_dir"], job["out_dir"], job["labels_dir"], cfg, on_progress=emit)
        result = dict(status="done", n_logs=s["n_logs"], n_errors=s["n_errors"], total_s=s["total_s"],
                      mean_metrics=s["mean_metrics"])
    except Exception as e:                                          # e.g. Tesseract missing, no logs found
        emit(dict(event="fatal", error=f"{type(e).__name__}: {e}", traceback=traceback.format_exc()))
        result = dict(status="failed", error=f"{type(e).__name__}: {e}")
    (run / "result.json").write_text(json.dumps(result))
    events.close()
    return 0 if result["status"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
