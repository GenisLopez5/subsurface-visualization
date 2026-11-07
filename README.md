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
cp .env.example .env    # then fill in DATABASE_URL (for /api/v1)
.venv/bin/python -m lithology serve --annotations /path/to/bbox_annotation --ui webui --port 8000
# interactive API docs: http://127.0.0.1:8000/docs
```

`webui/` holds two pages: `index.html` (the original UI: single log, zoom, batch on a server folder;
uses `/api/...`, local files) and `v1.html` (a minimal test page for the `/api/v1` file API: upload,
status by file, downloads, previews, delete; everything stored in Postgres).

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

## File API for frontends: `/api/v1`

Uploads, processing status and results, all stored in Postgres (e.g. Tiger Data). Set
`DATABASE_URL=postgres://user:pass@host:port/db?sslmode=require` in the environment or in a `.env`
file (looked up from the current folder upwards; never commit it). Without it, `/api/v1` answers 503
and everything else still works. Browser frontends on other origins: set `LITHO_CORS_ORIGINS`
(comma-separated, default `http://localhost:3000,http://localhost:5173`, `*` for any).

The flow is upload, poll by id, then download:

```bash
# 1. upload one image or many (+ their JSONs if you have them). Returns at once, with an id per file
curl -F files=@log1.tif -F files=@log2.tif -F files=@log1.json -F name="my batch" \
     http://127.0.0.1:8000/api/v1/uploads
# 2. ask whether they have finished
curl "http://127.0.0.1:8000/api/v1/status?ids=<id1>,<id2>"
#    -> [{"id": ..., "status": "done", "computed": true, ...}, ...]
# 3. download the results
curl -OJ http://127.0.0.1:8000/api/v1/files/<id>/download/result_tif
curl -OJ http://127.0.0.1:8000/api/v1/files/<id>/download/result_csv
```

File status: `uploading` → `queued` (or `needs_annotation`) → `processing` → `done` | `error`.
`computed` is true once the result TIF and CSV are stored. Every file response also has
`outputs` (which of `original`, `annotation`, `result_tif`, `result_csv`, `result_json` exist) and their URLs.

| | |
|---|---|
| `POST /api/v1/uploads` | multipart `files` (images `.tif/.tiff/.pdf/.png/.jpg` and optional JSONs), optional `name` and `annotations`. Returns the batch, the files with their ids, `rejected` files and `unused_annotations`. |
| `GET /api/v1/status?ids=a,b,c` | light status of many files, for polling |
| `GET /api/v1/files/{id}` | full detail: status, error, stats, outputs, download and preview URLs |
| `GET /api/v1/files?status=&batch_id=&q=&limit=&offset=` | list/search files |
| `GET /api/v1/files/{id}/download/{kind}` | `original`, `annotation`, `result_tif`, `result_csv` or `result_json` (`?inline=true` to show instead of download) |
| `GET /api/v1/files/{id}/preview/{kind}?width=1200` | PNG of `original` or `result_tif` for an `<img>` (browsers can't show TIFs) |
| `PUT /api/v1/files/{id}/annotation` | set/replace the bounding boxes (JSON body, or a JSON `file` in multipart) and process again |
| `POST /api/v1/files/{id}/reprocess?force=` | compute again (`force=true` also ignores the extraction cache) |
| `DELETE /api/v1/files/{id}` · `DELETE /api/v1/files?ids=a,b` | delete files (any status, even while processing) with all their data |
| `GET /api/v1/batches` · `GET /api/v1/batches/{id}` · `DELETE /api/v1/batches/{id}` | uploads with counts per status / with their files / delete everything in one |
| `GET /api/v1/health` | database, worker, queue length, what is processing now |

Where each image's bounding boxes come from (first match wins), recorded in `annotation_source`:
`uploaded` (a JSON in the same upload, paired by `image_name` or file name), `provided` (the
`annotations` form field: one annotation object, `{"<image file name>": annotation}` or a list
matched by `image_name`, e.g. from a box-extraction algorithm), `dataset` (existing JSONs from
`--annotations` folders), `detector` (see below). With none of them the file waits in
`needs_annotation` until `PUT .../annotation`.

Uploads are spooled to disk and stored in Postgres in the background, so the request returns at once.
Processing runs one file at a time from a queue kept in the database, so queued and interrupted files
are picked up again after a restart. Limit per file: `LITHO_MAX_UPLOAD_MB` (200).
Tables: `litho_batches`, `litho_files`, `litho_blobs` (created automatically).

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

- `results/<log>.csv`, `results/<log>.tif` (full resolution) and `results/summary.csv`
- `debug/<log>.json`: depth labels read, legend entries, per-interval confidence

Logs that already have results are skipped.

## Code

| module | |
|---|---|
| `extract.py` | the algorithm: depth calibration (OCR + robust line fit), legend parsing, column segmentation and matching |
| `annotations.py` | where the bounding boxes come from: `AnnotationStore` (existing JSONs), `BoxDetector` interface, `resolve_annotation()` |
| `notebook_viz.py` | the challenge notebook's drawing functions, copied verbatim (regenerate from the notebook, don't edit) |
| `drawing.py` | PNG previews with the notebook's `visualize_results`; full-resolution TIF with `render_tif` |
| `pipeline.py` | one log end to end, with an extraction cache |
| `batch.py` | folder scan, image/JSON pairing, batch run |
| `jobs.py` | background jobs (one extraction at a time) |
| `api.py` | FastAPI app |
| `files_api.py` | `/api/v1` file API for frontends |
| `db.py` | Postgres storage (batches, files, blobs) and the processing queue |
| `worker.py` | background upload storage and processing for `/api/v1` |
| `config.py` | settings |

### Adding automatic bounding-box detection

Implement `annotations.BoxDetector`: `detect(image_path) -> dict`, returning the same format as the
JSONs (`width`, `height`, `bounding_boxes` with `depth_column`, `interpreted_lithology_column`,
`lithology_legend`). For `/api/v1`, set `LITHO_DETECTOR=package.module:object` (an instance, a class or a
plain function `image_path -> dict`). Uploaded, provided and existing JSONs are still used first, and
the detector runs only when none matches. No other code needs to change.

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
