# Prompt for Claude Code: local web app for the lithology pipeline

Copy everything below the line into Claude Code, opened in the repo root.

---

## Context

I'm at a hackathon (HackTheBurgh, Viridien challenge). The task: take scanned well-log images and extract the
**interpreted lithology column** as structured data. That means splitting it into intervals, clustering them, naming each
cluster from the legend, and converting pixels to depth.

This repo already contains a working, tested pipeline:

- `lithology_pipeline.py`: the whole pipeline as one well-documented module, with a CLI and a library API.
  **Read it fully first.** It has a single method (no comparisons).
  - `run_folder(data_dir, out_dir, labels_dir=None, cfg=Config(), on_progress=callback)` processes every log in
    `data_dir`, writes everything to `out_dir`, and returns the same dict it writes to `out_dir/summary.json`.
  - `on_progress(event: dict)` events: `start` (n_logs), `log_start` (log, index, total), `stage` (log, stage, seconds;
    stages are column, segmentation, legend+naming, depth, render), `log_done` (n_intervals, runtime_s, metrics,
    ambiguous), `log_error`, `done` (total_s, n_errors, mean_metrics).
  - Output per log, in `out_dir`: `<stem>.csv` (submission format), `<stem>_page.png` (whole page, organisers'
    style), `<stem>_column.png` (zoomed depth + lithology columns), `<stem>_legend.png` (detected swatches, OCR names,
    look-alikes in red), `<stem>_depth.png` (depth OCR + RANSAC line).
  - `summary.json` holds `logs[]`, each with: `intervals` (top_y, bottom_y, depths, lithology, cluster_id, ambiguous),
    `clusters` (per-cluster name, top-3 swatch distances, margin, ambiguous, alternatives), `swatches` (+ look-alikes),
    `depth` (equation, labels used/read), `scale` (measured pattern period, window sizes), `colour_scan`, `k`,
    `silhouette`, `timings_s` per stage, `runtime_s`, `peak_mb`, `files`, and `metrics` when labels exist
    (row_acc, boundary_mean_px, boundary_max_px, boundary_f1, confusion). It also has `mean_metrics`, `errors` and
    `config`.
- `requirements.txt` + `setup_venv.sh`: `./setup_venv.sh` builds `.venv` with the Python packages **and a private
  Tesseract OCR copy inside `.venv/tesseract`** (launcher at `.venv/bin/tesseract`, no sudo; I'm on Ubuntu). Everything
  must stay inside `.venv`: no system-wide installs.
- `data/`: a sample log (`<stem>.tif` + `<stem>.json`) and the organisers' `example.csv`, which is ground truth for that
  log. The pipeline adopts it automatically.

Input format: each log is an image (`.tif`, sometimes `.png`/`.jpg`, can be 5000×100000 px and 100+ MB) plus a `.json`
with `image_name` and `bounding_boxes` (`depth_column`, `interpreted_lithology_column`, `lithology_legend`).
Optional ground truth goes in `labels/<stem>.csv`, with columns `top_depth,bottom_depth,lithology,top_y,bottom_y`.

## Goal

Build a **simple local web app** (http://localhost:8000) where I can upload logs, click Run, watch progress, and then
inspect everything I need to judge and demo the results: painted images, metrics, charts and per-interval details.
It's for me and my team during the hackathon. Make it clear and fast to use, not fancy.

## Hard rules

1. **Do not change the algorithm in `lithology_pipeline.py`.** Import it and call `run_folder`. If you truly need a hook,
   add it without changing any numbers the pipeline produces, and tell me exactly what you changed.
2. Stack: **FastAPI + uvicorn** backend, **plain HTML/CSS/JS** frontend served by FastAPI, no build step. Chart.js from a
   CDN is fine for interactive charts. Keep it to a handful of files.
3. Localhost only. Sanitise uploaded filenames (no path traversal). Serve files only from inside the runs folder.
4. Stream uploads to disk; never load a whole TIF into memory in the web layer.
5. Run the pipeline in a **background thread** (one job at a time is fine) so the UI stays responsive.

## Features

### 1. Upload
- Drag-and-drop (plus a file picker) for many files at once: images, `.json` annotations and `.csv` labels.
- Each new run gets its own folder: `runs/<YYYYmmdd-HHMMSS>/data/` (labels go to `data/labels/`, except `example.csv`,
  which stays in `data/`).
- After upload, show a **pre-flight table**, one row per log: image ✓/✗, json ✓/✗ (valid `bounding_boxes`?),
  labels ✓/– (optional), and file size. Show clear errors for unmatched files.
- Option to add files to the current run before running.

### 2. Run
- A Run button (plus an "advanced" fold with a few `Config` fields: auto_scale on/off, tie_ratio).
- Show a banner if Tesseract is missing (call `lithology_pipeline.check_tesseract()` at startup), telling me to run
  `./setup_venv.sh`.

### 3. Live monitoring
- Progress bar (logs done / total), current log + current stage, elapsed time.
- A live event log built from the `on_progress` events: legend swatches found, depth found or not, per-method runtime,
  interval count and accuracy when labels exist, errors. Use **Server-Sent Events** (or polling every 1 s if simpler).
- Errors per log in red, without stopping the batch (the pipeline already catches per-log errors).

### 4. Results dashboard (when the run finishes)
- **Summary cards:** logs processed, errors, total runtime, and (if any log has labels) mean row accuracy, mean
  boundary error (px) and boundary F1. If no log has labels, say so.
- **Logs table** (sortable): log, intervals, k, colour scan yes/no, pattern period, ambiguous names, runtime, peak memory,
  and accuracy / boundary error when labelled. Clicking a row opens the per-log view.
- **Charts** (Chart.js): runtime per stage per log (stacked bar), and row accuracy + boundary error per labelled log.
  Bars start at zero, with tooltips and value labels. Show the confusion matrix (from `metrics.confusion`) as a small
  heat-table.
- **Per-log view:**
  - **Image viewer** for `<stem>_column.png` (default) and `<stem>_page.png`. These images are very tall: give it
    zoom in/out/fit buttons, scrolling, and click-to-open full size. No heavy libraries.
  - `<stem>_legend.png` (look-alikes in red) and `<stem>_depth.png` with the depth equation and labels used/read.
  - **Intervals table:** top_y, bottom_y, top/bottom depth, lithology, cluster; ambiguous rows highlighted with a "?"
    tooltip listing the alternatives from `clusters`.
  - **Cluster details:** each cluster's chosen name, top-3 swatch distances, margin, ambiguous yes/no.
  - Scale info (pattern period, window), stage timings, peak memory, and a CSV download button.
- **Run history:** list previous runs from `runs/` (date, number of logs, mean accuracy if any) and reopen any of them.

### 5. Small "How it works" panel
A short text with one line per pipeline stage, taken from the module docstring (stages 1-8), so a judge can follow
along during the demo.

## Suggested structure

```
webapp/
  app.py            # FastAPI: upload, preflight, run, events (SSE), results, files, history
  static/index.html
  static/app.js
  static/style.css
requirements-web.txt   # fastapi, uvicorn[standard], python-multipart
run.sh                 # runs ./setup_venv.sh if .venv is missing, pip-installs requirements-web.txt into .venv, starts uvicorn
README.md              # setup + usage in 10 lines
tests/test_webapp.py   # pytest with FastAPI TestClient
```

API sketch (adjust if you have a better design):
`POST /api/runs` (new run) · `POST /api/runs/{id}/files` (multipart upload) · `GET /api/runs/{id}/preflight` ·
`POST /api/runs/{id}/start` (optional config overrides) · `GET /api/runs/{id}/events` (SSE) · `GET /api/runs/{id}/status` ·
`GET /api/runs/{id}/summary` · `GET /api/runs/{id}/files/{path}` · `GET /api/runs` (history) · `GET /api/health`
(tesseract present?).

## How to work

1. Read `lithology_pipeline.py` and run the CLI first, so you know the pipeline works here:
   `python lithology_pipeline.py data --out /tmp/cli_check`. Expect ≈ 99.5% row accuracy on the sample log.
   If it fails, stop and tell me why before building anything.
2. Show me a short plan (files, endpoints, page layout) and then build it in small steps.
3. Test:
   - pytest with TestClient: upload the sample files → preflight → start → wait for done → the summary's row accuracy
     matches the CLI's; an upload with a broken JSON is reported in preflight; a log without labels runs and shows no
     accuracy; path traversal (`../`) is rejected.
   - Start the server and check the main page loads and the image/CSV links resolve (curl is fine).
4. Finish with `./run.sh` working from a fresh clone, and tell me the URL plus what to click.

Keep the UI clean and readable: neutral colours, one accent, clear headings, tables with aligned numbers.
Prefer simple and robust over clever.
