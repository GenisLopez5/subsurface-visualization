# Lithology pipeline + Lithology Console

Extracts the interpreted lithology column of scanned well logs (intervals, rock names from the legend, depths).
`lithology_pipeline.py` is the algorithm (CLI + library); `webapp/` is a local web app to run it and inspect results.

## Web app (recommended)

```bash
./run.sh                      # first time: builds .venv (+ private Tesseract) and installs the web packages
# open http://localhost:8000  (PORT=8001 ./run.sh for another port; Ctrl+C to stop)
```

1. **New run**: drop files (one log or many: image + `.json`, optional labels `.csv`), *or* link a folder on this
   computer (the organisers' `dataset/` works as is, nothing is copied), *or* open an existing `outputs/` folder.
2. **Files & settings**: the pre-flight table checks every log (image, JSON boxes, labels); tick the logs to run.
   Every pipeline parameter can be changed; presets can be saved. Press **Start run**.
3. **Monitor**: progress bar, current log and stage, ETA, memory, live event log, **Cancel**.
4. **Results**: summary cards, flags (no legend, no depth, …), sortable/filterable table, runtime and accuracy charts,
   confusion matrix. Click a log for the zoomable column/page viewer, intervals, clusters, legend and depth fit.
5. **Clone & re-run** a run to try other settings on the same files. Downloads: per log an Output zip (CSV + annotated TIF); per run CSVs (.zip) or everything.

Runs live in `runs/<YYYYmmdd-HHMMSS>/`. Accuracy needs ground truth in a labels folder (`<stem>.csv`) or the
organisers' `example.csv`.

## Files

```
lithology_pipeline.py      entry point: Config (every setting), method dispatch, batch runner, CLI
methods/                   the extraction methods (default "automatic_lithology_cv"; see methods/__init__.py)
setup_auto_boxes.sh        one-time install of the YOLO box detector (bbox-final/.venv) for automatic bounding boxes
webapp/                    the web app: app.py (FastAPI server), worker.py (runs a batch), static/ (page)
labels/                    ground-truth CSVs used for accuracy and the Evaluation page
tests/                     web app + regression tests
runs/, outputs*/           results (not in git)
```

## Command line

```bash
.venv/bin/python lithology_pipeline.py <data_dir> --labels labels --out outputs
```

## Tests

```bash
.venv/bin/python -m pytest tests -v       # runs the real pipeline on the example log (~1 min)
```
