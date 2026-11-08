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
status by file, ZIP downloads, previews, delete; everything stored in Postgres).

| flag | meaning |
|---|---|
| `--annotations DIR ...` | folders with the bounding-box JSONs used to look up an uploaded image |
| `--images DIR ...` | folders with log images (used when only a JSON is uploaded) |
| `--data-dir DIR` | working folder: uploads, extraction cache, temp files (default `./lithology-data`) |
| `--ui DIR` | also serve a web UI (`index.html`) from this folder at `/` |
| `--split FILE` | `split.json`, to tag logs train/test in `/api/dataset` |
| `--desktop` | enable the folder-dialog / open-file endpoints (only when the server runs on the user's machine) |

The same settings can be set with environment variables: `LITHO_ANNOTATION_DIRS`, `LITHO_IMAGE_DIRS`
(separated by `:`, `;` on Windows), `LITHO_DATA_DIR`, `LITHO_UI_DIR`, `LITHO_SPLIT_FILE`, `LITHO_DESKTOP=1`.

**Bounding boxes are detected automatically** by a separate service, so the heavy models can run on
another machine. `bbox-final/server.py` serves the int8 OpenVINO models (two YOLO11s detectors: depth +
lithology column, then a legend specialist) at `POST /detect`:

```bash
# on the model machine (copy the bbox-final folder there). First run creates .venv and installs it all
bbox-final/run_server.sh                # port 8100 (PORT=...), check: curl http://<ip>:8100/health
# on the app machine, in the environment or .env
LITHO_BBOX_URL=http://<model machine ip>:8100
```

The app does the preprocessing (PDFs are rendered to a PNG) and sends the image; boxes come back in
original-image pixels. Order: a JSON sent with the image wins, then the service, then a matching JSON from
`--annotations` if the service fails or is unreachable. Without `LITHO_BBOX_URL` there is no automatic
detection (`LITHO_BBOX_LOCAL=1` runs the models in-process instead, if torch/ultralytics/openvino are
installed). `LITHO_BBOX_TIMEOUT` (default 300 s) bounds each call.

### Two ways to run it

- **All on the Windows GPU machine:** double-click `run_windows.bat`. It opens the bounding-box service in
  its own window (`bbox-final\run_server.bat`: NVIDIA GPU with the `.pt` weights in `bbox-final\models-pt\`,
  else Intel GPU, else CPU) and starts the main server + web UI on port 8000, calling the service at
  `127.0.0.1:8100`. First run creates both venvs. Other machines reach the UI at `http://<its ip>:8000`.
- **App on the laptop, models on Windows:** run `bbox-final\run_server.bat` (or `run_windows.bat`) on
  Windows, then the laptop's `run_ui.sh` / `runserver`, which point `LITHO_BBOX_URL` at the Windows
  machine's Tailscale IP.

On Windows, path lists in `LITHO_ANNOTATION_DIRS` / `LITHO_IMAGE_DIRS` are separated by `;`, and the
`--desktop` folder dialog uses Tk instead of zenity.

`LITHO_LEGEND_METHOD` picks the legend parser used for extraction: `grid` (default), `boxes`, `xycut`
or `old`. It is a server setting only (API users can't choose it). Changing it doesn't reuse cached
results. Compare the methods on the review page, `/legend.html`.

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
| `output` (query) | `zip` (default), or `json`: `{intervals, layout, csv, annotated_tif_url, csv_url}` |
| `scale` (query) | size of the annotated image (default: whole log at most ~12,000 px tall) |
| `force` (query) | ignore cached results |

The request blocks until processing ends, usually 5–60 s per log. Errors are JSON with an HTTP
error code, e.g. `{"error": "No JSON found for log.tif", "need": "json"}`.

`top_y` / `bottom_y` in the CSV are pixel rows of the **original** image, like the bounding boxes.
The annotated TIF is drawn at `scale`, with the colour key added on top, so its rows are shifted (see
[Drawn image vs original](#drawn-image-vs-original-the-y-shift)).

```python
import requests, io, zipfile
r = requests.post("http://127.0.0.1:8000/api/process", files={"file": open("log.tif", "rb")})
r.raise_for_status()
z = zipfile.ZipFile(io.BytesIO(r.content))
```

## Drawn image vs original: the y shift

Every drawn image (annotated / result TIF, previews, overview and zoom PNGs) is the original page,
resized by `scale`, with the **colour key bar added on top**. So it is taller than the original and
everything is pushed down by the bar's height. All coordinates the API returns (`top_y`, `bottom_y`,
the bounding boxes) are in **original-image** pixels. To place them on a drawn image:

```
y_drawn = legend_height_px + (y_original - y_start) * scale
x_drawn = x_original * scale                    # no horizontal shift
```

| field | meaning |
|---|---|
| `legend_height_px` | height of the colour key bar on top, in drawn-image px (the shift) |
| `scale` | drawn px per original px (`1` for the full-resolution TIF) |
| `y_start` | first original row drawn (`0` unless the image is a strip, e.g. `zoom.png`) |
| `original_width`, `original_height` | size of the original page |
| `image_width`, `image_height` | size of the drawn image (`image_height = legend_height_px + drawn page height`) |

Where you get it:

| endpoint | as |
|---|---|
| `POST /api/process?output=json` | `layout` (of the annotated TIF) |
| `POST /api/process` (zip) | headers `X-Legend-Height`, `X-Scale`, `X-Y-Start` |
| `GET /api/jobs/{id}` | `layout` (of `overview.png`) |
| `GET /api/jobs/{id}/overview.png` · `zoom.png` · `annotated.tif` | the same headers |
| `GET /api/v1/files/{id}` | `stats.layout` (of the result TIF) |
| `GET /api/v1/files/{id}/preview/result_tif?width=` | headers `X-Legend-Height`, `X-Scale`, `X-Y-Start`, already scaled to the preview's width |

The headers are exposed to CORS clients, so a browser frontend can read them with
`response.headers.get("X-Legend-Height")`. The layout is also saved next to every drawn image on disk
as `<image>.layout.json`.

```js
const r = await fetch(`/api/v1/files/${id}/preview/result_tif?width=1200`);
const shift = +r.headers.get("X-Legend-Height"), scale = +r.headers.get("X-Scale");
const toPreviewY = (yOriginal) => shift + yOriginal * scale;   // y_start is 0 here
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
curl -F files=@log1.tif -F files=@log2.tif -F files=@log1.json \
     http://127.0.0.1:8000/api/v1/uploads
# 2. ask whether they have finished
curl "http://127.0.0.1:8000/api/v1/status?ids=<id1>,<id2>"
#    -> [{"id": ..., "status": "done", "computed": true, ...}, ...]
# 3. download the results
curl -OJ http://127.0.0.1:8000/api/v1/files/<id>/download            # original + TIF + CSV as one ZIP
curl -OJ 'http://127.0.0.1:8000/api/v1/download?ids=<id1>,<id2>'     # several files, one folder each
curl -OJ http://127.0.0.1:8000/api/v1/download/all                   # every file in the database
```

File status: `uploading` → `queued` (or `needs_annotation`) → `processing` → `done` | `error`.
`computed` is true once the result TIF and CSV are stored. Every file response also has
`outputs` (which of `original`, `annotation`, `result_tif`, `result_csv`, `result_json`, `legend_original`, `legend_generated` exist), `preview` URLs and `download` (the URL of its ZIP, `null` until finished).

| | |
|---|---|
| `POST /api/v1/uploads` | multipart `files` (images `.tif/.tiff/.pdf/.png/.jpg` and optional JSONs), optional `annotations`. Each image becomes its own independent file. Returns the files with their ids, `rejected` files and `unused_annotations`. |
| `GET /api/v1/status?ids=a,b,c` | light status of many files, for polling |
| `GET /api/v1/files/{id}` | full detail: status, error, stats, outputs, download and preview URLs |
| `PATCH /api/v1/files/{id}` | rename an item: JSON `{"name": "new name"}` (1–200 characters, no `/`, `\` or control characters). Downloads are named after it from then on. Any status; results are kept. |

Item names are always returned **without the file extension** (`log.tif` → `log`), in every response.
| `GET /api/v1/files?status=&q=&limit=&offset=` | list/search files, newest first |
| `GET /api/v1/files/{id}/result` | the result TIF, the result CSV and the **offset** (result TIF height − original height: the colour key bar on top; `y_result = offset_y_px + y_original`). Default: `<name>_solved.zip` with `<name>_solved.tif`, `<name>_solved.csv`, `<name>_offset.json`, `<name>_legend_original.png` (the log's own legend, cut from the original at the `lithology_legend` box, given as `offset.legend_original_box`) and `<name>_legend_generated.png` (the colour key bar on top of the result TIF: same width, height = `offset_y_px`), plus an `X-Offset-Y` header. `?format=json`: `{id, name, offset, csv: {filename, content}, result_tif, legend_original, legend_generated: {filename, base64}}`. The legends are missing/`null` for results computed before they existed (reprocess). 404 until finished. |
| `GET /api/v1/files/{id}/download` | `<name>_solved.zip` with the original image `<name>.tif`, the result TIF `<name>_solved.tif` and the result CSV `<name>_solved.csv` (404 until all three exist). `<name>` is the file name as uploaded, spaces included. |
| `GET /api/v1/download?ids=a,b,c` | ZIP with one folder per file (named after the file), each holding its original image, result TIF and result CSV. Files without finished results are left out and listed in `skipped.txt`; 404 if an id is unknown or none is finished. At most 1000 ids. |
| `GET /api/v1/download/all` | the same for every file in the database |
| `GET /api/v1/files/{id}/preview/{kind}?width=1200` | PNG of `original`, `result_tif`, `legend_original` or `legend_generated` for an `<img>` (browsers can't show TIFs) |
| `PUT /api/v1/files/{id}/annotation` | set/replace the bounding boxes (JSON body, or a JSON `file` in multipart) and process again |
| `POST /api/v1/files/{id}/reprocess?force=` | compute again (`force=true` also ignores the extraction cache) |
| `DELETE /api/v1/files/{id}` · `DELETE /api/v1/files?ids=a,b` | delete files (any status, even while processing) with all their data |
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
Tables: `litho_files` (one row per file) and `litho_blobs` (its original, annotation and results), created automatically.

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
| `db.py` | Postgres storage (files, blobs) and the processing queue |
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
   direction in 4 bins. The default `grid` parser (`legend_grid.py`) groups the swatches into columns,
   reads the text one column strip at a time, and gives each text line to the swatch on its row.
   `legend_boxes.py` holds the alternatives `boxes` (proximity grouping) and `xycut` (whitespace cuts).
3. **Column:** cut `interpreted_lithology_column` where a sliding window's best-matching legend entry
   changes, or where the fill colour changes. Then name each piece by its closest legend fingerprint,
   using all of its pixels.
4. **Output:** convert rows to depths, then write the CSV and the drawing.

Known limits:

- On scanned B/W logs, two near-identical legend patterns (e.g. Clay vs Shale) can be confused. The
  boundaries are still right.
- Legends whose patterns are made of separate symbols can get garbled names.
