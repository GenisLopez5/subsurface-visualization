#!/usr/bin/env python3
"""Profile the complete lithology pipeline and report a stage-by-stage breakdown.

The profiler uses the pipeline's own stage timings and adds wall-clock time plus
peak RSS for the parent process and all child processes (box detector and OCR).
It writes:

  profile.json   machine-readable measurements
  stages.csv     one row per document and stage
  profile.md     compact report suitable for slides or documentation
  run-XX/        the pipeline's normal outputs and summary.json

Example:
  .venv/bin/python profile_pipeline.py ../data --out ../profiling/example
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil


HERE = Path(__file__).resolve().parent
REPO = HERE.parent
STAGE_LABELS = {
    "boxes": "Column + legend detection",
    "column": "Column preprocessing",
    "legend+naming": "Legend extraction + OCR",
    "segmentation": "Subsurface prediction",
    "depth": "Depth OCR + calibration",
    "render": "OpenCV rendering + outputs",
}
DETECTOR_LABELS = {
    "startup+orchestration": "Model startup + orchestration",
    "general_model": "General column model",
    "legend_model+geometry": "Legend specialist + geometry",
}
LEGEND_READER_LABELS = {
    "size-groups": "Tesseract size groups",
    "text-anchored": "Tesseract text anchored",
    "grid": "RapidOCR grid reader",
    "rapidocr": "RapidOCR fallback",
    "other_legend_work": "Crop + swatch processing",
}


def percentile(values: list[float], q: float) -> float:
    """Linearly interpolated percentile without a NumPy dependency here."""
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


class ProcessTreeSampler:
    """Sample RSS and CPU time for this process and its live descendants."""

    def __init__(self, interval_s: float = 0.05):
        self.interval_s = interval_s
        self.root = psutil.Process()
        self.peak_rss_bytes = 0
        self.peak_processes = 0
        self.max_observed_cpu_s = 0.0
        self.samples = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _sample(self) -> None:
        try:
            processes = [self.root, *self.root.children(recursive=True)]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return
        rss = 0
        cpu_s = 0.0
        alive = 0
        for process in processes:
            try:
                rss += process.memory_info().rss
                cpu = process.cpu_times()
                cpu_s += cpu.user + cpu.system
                alive += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        self.peak_rss_bytes = max(self.peak_rss_bytes, rss)
        self.peak_processes = max(self.peak_processes, alive)
        self.max_observed_cpu_s = max(self.max_observed_cpu_s, cpu_s)
        self.samples += 1

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            self._sample()
        self._sample()

    def __enter__(self) -> "ProcessTreeSampler":
        self._sample()
        self._thread = threading.Thread(target=self._run, name="pipeline-resource-sampler", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=max(1.0, self.interval_s * 4))


def lscpu_info() -> dict[str, str]:
    wanted = {
        "Architecture": "architecture",
        "CPU(s)": "logical_cpus",
        "Model name": "model_name",
        "Thread(s) per core": "threads_per_core",
        "Core(s) per socket": "cores_per_socket",
        "Socket(s)": "sockets",
        "CPU max MHz": "cpu_max_mhz",
        "CPU min MHz": "cpu_min_mhz",
        "L3": "l3_cache",
    }
    try:
        result = subprocess.run(["lscpu", "--json"], check=True, capture_output=True, text=True)
        fields = json.loads(result.stdout)["lscpu"]
        raw = {entry["field"].rstrip(":"): str(entry.get("data", "")) for entry in fields}
        return {out: raw[name] for name, out in wanted.items() if raw.get(name)}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return {
            "architecture": platform.machine(),
            "logical_cpus": str(os.cpu_count() or "unknown"),
            "model_name": platform.processor() or "unknown",
        }


def stage_rows(run_index: int, logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for log in logs:
        timings = {str(k): float(v) for k, v in (log.get("timings_s") or {}).items()}
        measured_total = sum(timings.values())
        for stage, seconds in timings.items():
            rows.append({
                "run": run_index,
                "document": log.get("stem", "unknown"),
                "stage": stage,
                "stage_label": STAGE_LABELS.get(stage, stage),
                "seconds": round(seconds, 3),
                "percent_of_measured_log": round(100 * seconds / measured_total, 2) if measured_total else 0.0,
            })
    return rows


def aggregate_stages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_stage: dict[str, list[float]] = {}
    for row in rows:
        by_stage.setdefault(str(row["stage"]), []).append(float(row["seconds"]))
    total = sum(sum(values) for values in by_stage.values())
    order = ["boxes", "column", "legend+naming", "segmentation", "depth", "render"]
    stages = [*order, *sorted(set(by_stage) - set(order))]
    return [{
        "stage": stage,
        "label": STAGE_LABELS.get(stage, stage),
        "samples": len(by_stage[stage]),
        "mean_s": round(statistics.fmean(by_stage[stage]), 3),
        "median_s": round(statistics.median(by_stage[stage]), 3),
        "p90_s": round(percentile(by_stage[stage], 0.90), 3),
        "sum_s": round(sum(by_stage[stage]), 3),
        "share_percent": round(100 * sum(by_stage[stage]) / total, 2) if total else 0.0,
    } for stage in stages if stage in by_stage]


def aggregate_named(samples: list[dict[str, float]], labels: dict[str, str]) -> list[dict[str, Any]]:
    by_name: dict[str, list[float]] = {}
    for sample in samples:
        for name, seconds in sample.items():
            by_name.setdefault(name, []).append(float(seconds))
    total = sum(sum(values) for values in by_name.values())
    order = [*labels, *sorted(set(by_name) - set(labels))]
    return [{
        "name": name,
        "label": labels.get(name, name),
        "samples": len(by_name[name]),
        "mean_s": round(statistics.fmean(by_name[name]), 3),
        "median_s": round(statistics.median(by_name[name]), 3),
        "p90_s": round(percentile(by_name[name], 0.90), 3),
        "sum_s": round(sum(by_name[name]), 3),
        "share_percent": round(100 * sum(by_name[name]) / total, 2) if total else 0.0,
    } for name in order if name in by_name]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = ["run", "document", "stage", "stage_label", "seconds", "percent_of_measured_log"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    runtime = report["runtime"]
    machine = report["machine"]
    lines = [
        "# End-to-end pipeline profile",
        "",
        f"- Input: `{report['input_dir']}`",
        f"- Method: `{report['method']}`",
        f"- Documents: **{report['documents_completed']}** completed / **{report['documents_requested']}** requested",
        f"- Total wall time: **{runtime['wall_s']:.2f} s**",
        f"- Median per document: **{runtime['median_document_s']:.2f} s**",
        f"- P90 per document: **{runtime['p90_document_s']:.2f} s**",
        f"- Peak process-tree RSS: **{runtime['peak_process_tree_rss_mb']:.1f} MB**",
        f"- Hardware: **{machine.get('model_name', 'unknown')}**, "
        f"{machine.get('cores_per_socket', '?')} cores / {machine.get('logical_cpus', '?')} threads",
        "",
        "## Stage breakdown",
        "",
        "| Stage | Mean | Median | P90 | Share |",
        "|---|---:|---:|---:|---:|",
    ]
    for stage in report["stage_summary"]:
        lines.append(
            f"| {stage['label']} | {stage['mean_s']:.2f} s | {stage['median_s']:.2f} s | "
            f"{stage['p90_s']:.2f} s | {stage['share_percent']:.1f}% |"
        )
    if report["detector_summary"]:
        lines.extend([
            "",
            "## Detection breakdown",
            "",
            "| Detector step | Mean | Share of detection |",
            "|---|---:|---:|",
        ])
        for item in report["detector_summary"]:
            lines.append(f"| {item['label']} | {item['mean_s']:.2f} s | {item['share_percent']:.1f}% |")
    if report["legend_reader_summary"]:
        lines.extend([
            "",
            "## Legend extraction breakdown",
            "",
            "| Legend step | Mean | Share of legend stage |",
            "|---|---:|---:|",
        ])
        for item in report["legend_reader_summary"]:
            lines.append(f"| {item['label']} | {item['mean_s']:.2f} s | {item['share_percent']:.1f}% |")
    lines.extend([
        "",
        "> Depth OCR runs concurrently with column, legend and segmentation work. Its stage value is the remaining wait, "
        "so the stage rows partition wall time instead of double-counting parallel work.",
        "",
        "> Peak RSS samples the profiler, detector and OCR child processes every 50 ms. Very short peaks may be missed.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_dir", type=Path, help="Folder containing image + bounding-box JSON pairs")
    parser.add_argument("--out", type=Path, default=REPO / "profiling" / "latest", help="Profile output directory")
    parser.add_argument("--labels", type=Path, default=None, help="Optional ground-truth CSV directory")
    parser.add_argument("--method", default="automatic_lithology_cv", choices=(
        "automatic_lithology_cv", "lithology_cv", "hybrid_v3", "hybrid_v2", "hybrid", "lithology", "cv"))
    parser.add_argument("--repeats", type=int, default=1, help="Number of measured runs (default: 1)")
    parser.add_argument("--annotated-tif", action="store_true", help="Include full-resolution annotated TIFF rendering")
    parser.add_argument("--bbox-python", type=Path, default=None,
                        help="Detector Python; defaults to bbox-final/.venv or .venv-int8")
    parser.add_argument("--sample-ms", type=float, default=50.0, help="Resource sampling interval in ms")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.repeats < 1:
        raise SystemExit("--repeats must be at least 1")
    if not args.data_dir.is_dir():
        raise SystemExit(f"data directory does not exist: {args.data_dir}")

    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(out_dir / ".matplotlib"))
    bbox_python = args.bbox_python
    if bbox_python is None:
        candidates = [REPO / "bbox-final/.venv/bin/python", REPO / "bbox-final/.venv-int8/bin/python"]
        bbox_python = next((path for path in candidates if path.is_file()), candidates[0])
    # Do not resolve the venv's python symlink: executing the resolved system
    # interpreter would bypass pyvenv.cfg and silently use the wrong packages.
    bbox_python = bbox_python if bbox_python.is_absolute() else Path.cwd() / bbox_python
    bbox_python = bbox_python.absolute()
    os.environ["LITHO_BBOX_PYTHON"] = str(bbox_python)

    sys.path.insert(0, str(HERE))
    from lithology_pipeline import Config, run_folder  # Imported after environment configuration.

    events: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    runs: list[dict[str, Any]] = []
    detector_samples: list[dict[str, float]] = []
    legend_reader_samples: list[dict[str, float]] = []
    started_utc = datetime.now(timezone.utc).isoformat()
    wall_started = time.perf_counter()
    parent_cpu_started = time.process_time()

    def progress(event: dict[str, Any]) -> None:
        events.append(dict(event))
        if event.get("event") == "stage":
            print(f"  {event.get('log')}: {event.get('stage')} {float(event.get('seconds', 0)):.3f}s", flush=True)
        elif event.get("event") in {"log_start", "log_error", "done"}:
            print(json.dumps(event, ensure_ascii=False, default=str), flush=True)

    with ProcessTreeSampler(max(0.01, args.sample_ms / 1000.0)) as resources:
        for run_index in range(1, args.repeats + 1):
            run_out = out_dir / f"run-{run_index:02d}"
            diagnostics_dir = run_out / "bbox-diagnostics"
            os.environ["LITHO_BBOX_DIAGNOSTICS_DIR"] = str(diagnostics_dir)
            print(f"RUN {run_index}/{args.repeats} -> {run_out}", flush=True)
            run_started = time.perf_counter()
            summary = run_folder(
                args.data_dir.resolve(),
                run_out,
                args.labels.resolve() if args.labels else None,
                Config(method=args.method, annotated_tif=args.annotated_tif),
                on_progress=progress,
            )
            run_wall = time.perf_counter() - run_started
            rows = stage_rows(run_index, summary.get("logs", []))
            all_rows.extend(rows)
            documents = []
            for log in summary.get("logs", []):
                timings = {str(k): float(v) for k, v in (log.get("timings_s") or {}).items()}
                diagnostics_path = diagnostics_dir / f"{Path(str(log.get('image', 'unknown'))).stem}.json"
                detector_parts: dict[str, float] = {}
                if diagnostics_path.is_file():
                    details = json.loads(diagnostics_path.read_text())
                    general_s = float((details.get("general") or {}).get("elapsed_seconds") or 0.0)
                    legend_s = float((details.get("legend") or {}).get("elapsed_seconds") or 0.0)
                    detector_parts = {
                        "startup+orchestration": round(max(0.0, timings.get("boxes", 0.0) - general_s - legend_s), 3),
                        "general_model": round(general_s, 3),
                        "legend_model+geometry": round(legend_s, 3),
                    }
                    detector_samples.append(detector_parts)
                reader_parts = {str(k): float(v) for k, v in (log.get("legend_reader_timings_s") or {}).items()}
                if reader_parts:
                    reader_parts["other_legend_work"] = round(
                        max(0.0, timings.get("legend+naming", 0.0) - sum(reader_parts.values())), 3)
                    legend_reader_samples.append(reader_parts)
                documents.append({
                    "stem": log.get("stem"),
                    "timings_s": log.get("timings_s", {}),
                    "measured_stage_total_s": round(sum(timings.values()), 3),
                    "pipeline_runtime_excluding_render_s": log.get("runtime_s"),
                    "python_heap_peak_mb": log.get("peak_mb"),
                    "bbox_diagnostics_path": str(diagnostics_path.resolve()) if diagnostics_path.is_file() else None,
                    "detector_breakdown_s": detector_parts,
                    "legend_reader_breakdown_s": reader_parts,
                })
            runs.append({
                "run": run_index,
                "wall_s": round(run_wall, 3),
                "pipeline_reported_total_s": summary.get("total_s"),
                "documents_requested": summary.get("n_logs", 0),
                "documents_completed": len(summary.get("logs", [])),
                "errors": summary.get("errors", []),
                "summary_path": str((run_out / "summary.json").resolve()),
                "documents": documents,
            })

    wall_s = time.perf_counter() - wall_started
    document_totals = [
        float(document["measured_stage_total_s"])
        for run in runs for document in run["documents"]
    ]
    report = {
        "schema_version": 1,
        "created_utc": started_utc,
        "input_dir": str(args.data_dir.resolve()),
        "output_dir": str(out_dir),
        "method": args.method,
        "repeats": args.repeats,
        "annotated_tif": args.annotated_tif,
        "bbox_python": str(bbox_python),
        "documents_requested": sum(int(run["documents_requested"]) for run in runs),
        "documents_completed": len(document_totals),
        "machine": {
            **lscpu_info(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "runtime": {
            "wall_s": round(wall_s, 3),
            "parent_python_cpu_s": round(time.process_time() - parent_cpu_started, 3),
            "median_document_s": round(statistics.median(document_totals), 3) if document_totals else 0.0,
            "mean_document_s": round(statistics.fmean(document_totals), 3) if document_totals else 0.0,
            "p90_document_s": round(percentile(document_totals, 0.90), 3) if document_totals else 0.0,
            "peak_process_tree_rss_mb": round(resources.peak_rss_bytes / 1_000_000, 1),
            "peak_live_processes": resources.peak_processes,
            "resource_samples": resources.samples,
            "sampling_interval_ms": args.sample_ms,
            "max_observed_process_tree_cpu_s": round(resources.max_observed_cpu_s, 3),
        },
        "stage_summary": aggregate_stages(all_rows),
        "detector_summary": aggregate_named(detector_samples, DETECTOR_LABELS),
        "legend_reader_summary": aggregate_named(legend_reader_samples, LEGEND_READER_LABELS),
        "runs": runs,
        "measurement_notes": [
            "End-to-end wall time includes model startup, OCR, OpenCV work and normal output rendering.",
            "Depth OCR runs in parallel; its stage timing is only the remaining wait after segmentation.",
            "Peak RSS covers the profiler and currently alive descendants; peaks shorter than the sample interval may be missed.",
            "The default run does not generate the optional full-resolution annotated TIFF unless --annotated-tif is set.",
        ],
    }
    (out_dir / "profile.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    write_csv(out_dir / "stages.csv", all_rows)
    write_markdown(out_dir / "profile.md", report)

    print("\nSTAGE BREAKDOWN", flush=True)
    for stage in report["stage_summary"]:
        print(f"  {stage['label']:<30} {stage['mean_s']:>8.2f}s  {stage['share_percent']:>6.1f}%", flush=True)
    print(f"TOTAL WALL                         {report['runtime']['wall_s']:>8.2f}s", flush=True)
    print(f"PEAK PROCESS-TREE RSS              {report['runtime']['peak_process_tree_rss_mb']:>8.1f} MB", flush=True)
    print(f"REPORT                             {out_dir / 'profile.md'}", flush=True)
    return 1 if any(run["errors"] for run in runs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
