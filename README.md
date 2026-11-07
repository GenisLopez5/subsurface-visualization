# lithology

Reads mud-log images and returns the **lithology intervals** as a CSV (`top_depth, bottom_depth,
lithology, top_y, bottom_y, cluster_id`, same as the challenge's `example.csv`), plus the log
**annotated** by the challenge notebook's `visualize_results()`. Runs as an HTTP API, a batch job
or a command-line tool.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt      # or: pip install -e .
```

## Run the API

```bash
.venv/bin/python -m lithology serve --annotations /path/to/bbox_annotation --port 8000
# interactive API docs: http://127.0.0.1:8000/docs
```

| flag | meaning |
|---|---|
| `--annotations DIR ...` | folders with the bounding-box JSONs used to look up an uploaded image |
| `--images DIR ...` | folders with log images (used when only a JSON is uploaded) |
| `--data-dir DIR` | working folder: uploads, extraction cache, temp files (default `./lithology-data`) |
| `--ui DIR` | also serve a web UI (`index.html`) from this folder at `/` |
| `--split FILE` | `split.json`, to tag logs train/test in `/api/dataset` |
| `--desktop` | enable the folder-dialog / open-file endpoints (only when the server runs on the user's machine) |

The same settings can be set with environment variables: `LITHO_ANNOTATION_DIRS`, `LITHO_IMAGE_DIRS`
(separated by `:`), `LITHO_DATA_DIR`, `LITHO_UI_DIR`, `LITHO_SPLIT_FILE`, `LITHO_DESKTOP=1`.

## Main endpoint: `POST /api/process`

Send a log image. You get back the annotated TIF and the CSV.

```bash
curl -F "file=@log.tif" http://127.0.0.1:8000/api/process -o result.zip
# result.zip: log_annotated.tif  log_lithology.csv
```

| parameter | |
|---|---|
| `file` (form, required) | `.tif` / `.tiff` / `.pdf` / `.png` |
| `annotation` (form, optional) | the bounding-box JSON. If omitted, the matching JSON is looked up in `--annotations` by file name and image size |
| `output` (query) | `zip` (default), or `json`: `{intervals, csv, annotated_tif_url, csv_url}` |
| `scale` (query) | size of the annotated image (default: whole log at most ~12,000 px tall) |
| `force` (query) | ignore cached results |

The request blocks until processing ends, usually 5–60 s per log. Errors are JSON with an HTTP
error code, e.g. `{"error": "No JSON found for log.tif", "need": "json"}`.

`top_y` / `bottom_y` in the CSV are pixel rows of the **original** image, like the bounding boxes.
The annotated TIF is drawn at `scale`.

```python
import requests, io, zipfile
r = requests.post("http://127.0.0.1:8000/api/process", files={"file": open("log.tif", "rb")})
r.raise_for_status()
z = zipfile.ZipFile(io.BytesIO(r.content))
```

## Other endpoints

Used by the web UI. Full list with parameters at `/docs`.

| | |
|---|---|
| `POST /api/jobs` | start a job (fields `file` (1–2 files: image and/or JSON), `keep`, `dataset`, `force`) and return its id at once |
| `GET /api/jobs/{id}` | status, progress log, summary, intervals |
| `GET /api/jobs/{id}/overview.png` · `zoom.png?top=&bottom=` · `annotated.tif` · `result.csv` · `result.json` | outputs |
| `POST /api/jobs/{id}/rerun` | same inputs, ignoring the cache |
| `GET /api/dataset` | annotations in the store |
| `POST /api/batch/scan` · `POST /api/batch` · `GET /api/batch/{id}` · `POST /api/batch/{id}/stop` | batch on a server folder |
| `POST /api/desktop/pick-folder` · `POST /api/desktop/open` | with `--desktop` only |

Job state is kept in memory and is lost on restart. Results on disk and the cache are kept.

## Batch and command line

```bash
.venv/bin/python -m lithology batch FOLDER [--redo]        # FOLDER/{images,jsons} -> FOLDER/results, FOLDER/debug
.venv/bin/python -m lithology process log.tif --annotations DIR -o out/
.venv/bin/python -m lithology extract log.tif log.json -o result.json --csv result.csv -v
```

Batch: the folder holds an image sub-folder and a JSON sub-folder (any names). It writes:

- `results/<log>.csv`, `results/<log>.png` and `results/summary.csv`
- `debug/<log>.json`: depth labels read, legend entries, per-interval confidence

Logs that already have results are skipped.

## Code

| module | |
|---|---|
| `extract.py` | the algorithm: depth calibration (OCR + robust line fit), legend parsing, column segmentation and matching |
| `annotations.py` | where the bounding boxes come from: `AnnotationStore` (existing JSONs), `BoxDetector` interface, `resolve_annotation()` |
| `notebook_viz.py` | the challenge notebook's drawing functions, copied verbatim (regenerate from the notebook, don't edit) |
| `drawing.py` | calls the notebook's `visualize_results` from temp files (page, JSON, CSV); PNG/TIF output |
| `pipeline.py` | one log end to end, with an extraction cache |
| `batch.py` | folder scan, image/JSON pairing, batch run |
| `jobs.py` | background jobs (one extraction at a time) |
| `api.py` | FastAPI app |
| `config.py` | settings |

### Adding automatic bounding-box detection

Implement `annotations.BoxDetector`: `detect(image_path) -> dict`, returning the same format as the
JSONs (`width`, `height`, `bounding_boxes` with `depth_column`, `interpreted_lithology_column`,
`lithology_legend`). Then pass it to `resolve_annotation(..., detector=...)` in `api.py`. Existing JSONs
are still used first, and the detector runs only when none matches. No other code needs to change.

## How the extraction works

1. **Depth:** OCR the numbers in `depth_column` and fit `depth = a·y + b` with RANSAC plus an
   outlier-trimming refit.
2. **Legend:** OCR the labels, find each label's pattern swatch, and fingerprint the swatch. The
   fingerprint holds the fill colour, the ink fraction, the edge density, and a histogram of stroke
   direction in 4 bins.
3. **Column:** cut `interpreted_lithology_column` where a sliding window's best-matching legend entry
   changes, or where the fill colour changes. Then name each piece by its closest legend fingerprint,
   using all of its pixels.
4. **Output:** convert rows to depths, then write the CSV and the drawing.

Known limits:

- On scanned B/W logs, two near-identical legend patterns (e.g. Clay vs Shale) can be confused. The
  boundaries are still right.
- Legends whose patterns are made of separate symbols can get garbled names.
