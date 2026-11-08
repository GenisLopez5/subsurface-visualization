<img width="128" height="128" alt="logo_subsurface_transparent" src="https://github.com/user-attachments/assets/330456a7-96e8-491a-b5c0-d87fad45bd4d" /> \

# HackTheBurgh 2026 🏴󠁧󠁢󠁳󠁣󠁴󠁿 - Viridien Challenge 🪨🌍
Our team took part in **HackTheBurgh 2026** in Edinburgh, where we built a solution for the challenge set by
**[Viridien](https://www.viridiengroup.com/)**: *lithology segmentation and clustering*.
Viridien is a geoscience technology company that provides data, imaging and digital solutions to the energy and
natural-resources industries.

We turn scanned geological well logs (TIFF, PNG, JPG or PDF) into structured data: **which rock sits at which depth**.
The project then lets you browse the results like a cloud drive and explore each well in 3D.


## The challenge

A *lithology column* is a vertical strip in a well log. It uses colours and patterns (dots, dashes and so on) to show
the rock type at each depth: shale, siltstone, sandstone and others. A lot of this data is stuck in PDFs and scans,
and extracting it by hand is slow and repetitive. The data is useful for geothermal, carbon storage, critical
minerals, hydrogen, and oil and gas.

For every document the solution must:

1. **Segment** the column into intervals (top and bottom boundary in pixels).
2. **Cluster** intervals that have the same or a similar pattern.
3. **Match** each cluster to the legend to get the rock name.
4. **Convert** pixel rows to real depths using the depth column.
5. **Bonus:** find the depth column, lithology column and legend automatically, without the bounding boxes supplied.

It delivers a **CSV** with one row per interval (`top_depth, bottom_depth, lithology, top_y, bottom_y, cluster_id`)
and an **annotated image** of the column. The judges look at correctness, generalisation, efficiency (runtime,
memory, hardware, cost), simplicity and full automation, with the stress on **correctness and efficiency**.


## Authors

- Arnau Claramunt
- Genís López
- Jaume López
- Pau Mayench 

[![GitHub followers](https://img.shields.io/github/followers/ArnauCS03?label=ArnauCS03)](https://github.com/ArnauCS03) &nbsp;&nbsp; [![GitHub followers](https://img.shields.io/github/followers/GenisLopez5?label=GenisLopez5)](https://github.com/GenisLopez5) &nbsp;&nbsp; [![GitHub followers](https://img.shields.io/github/followers/EncryptEx?label=EncryptEx)](https://github.com/EncryptEx) &nbsp;&nbsp; [![GitHub followers](https://img.shields.io/github/followers/PauMayench?label=PauMayench)](https://github.com/PauMayench) <br><br>


> [!NOTE]
> Everything runs **locally on a CPU**: no GPU, no paid API and no internet connection are needed once it is
> installed. The bounding-box models are int8 and run in seconds to a minute per page on a laptop.


## Project Technologies and Tools

- **Python**: the whole extraction pipeline and the servers.
- **OpenCV, NumPy, SciPy, scikit-learn, scikit-image**: classic computer vision. Ink masks, morphology,
  texture features, k-means clustering, colour analysis in the CIE Lab space.
- **Tesseract** and **RapidOCR** (PaddleOCR models on ONNX Runtime): reading the legend names and the depth numbers.
- **YOLO11 (Ultralytics) + PyTorch**: training the bounding-box detectors (on Google Colab GPUs).
- **OpenVINO (int8)**: fast CPU inference of the trained detectors.
- **pypdfium2 / libvips / Pillow**: reading huge TIFF scans and PDFs without running out of memory.
- **FastAPI + Uvicorn**: the REST APIs (extraction, file storage, bounding-box service) and the web app.
- **PostgreSQL** (Tiger Data): storage of uploaded documents, results and the processing queue.
- **HTML, CSS and plain JavaScript**: the *Lithology Console* web app (no framework, no build step).
- **Unity 6 (URP, C#)**: the document explorer and the 3D borehole visualizer.


## Table of Contents

- [Project Overview](#project-overview)
  - [1. Computer vision: from scan to intervals](#1-computer-vision-from-scan-to-intervals)
  - [2. YOLO: finding the three boxes automatically](#2-yolo-finding-the-three-boxes-automatically)
  - [3. Lithology Console: run, check and compare](#3-lithology-console-run-check-and-compare)
  - [4. Processing API and a Drive-style document explorer](#4-processing-api-and-a-drive-style-document-explorer)
  - [5. Unity: visualising the results in 3D](#5-unity-visualising-the-results-in-3d)
- [Repository layout](#repository-layout)
- [Results](#results)
- [Challenges we ran into](#challenges-we-ran-into)
- [Project Setup](#project-setup)
- [Learning](#learning-)
- [What's next](#whats-next)


## Project Overview

```
 scanned log ──► YOLO boxes ──► legend │ lithology column │ depth column ──► intervals ──► CSV + annotated image
 (TIF/PDF)       (or .json)     names    segment+cluster    OCR + line fit                     │
                                                                                              ▼
                                         Drive-style explorer (upload, library) ──► 3D borehole viewer (Unity)
```

### 1. Computer vision: from scan to intervals

The core is the method **`automatic_lithology_cv`** (`cv_approach_1/methods/lithology_cv_pipeline.py`). It reads a log
the way a geologist would: look up the patterns in the legend, follow the column from top to bottom, and read the
depth off the scale next to it.

1. **Boxes**: the YOLO detector (below) finds the depth column, the lithology column and the legend. If it fails,
   the organisers' `.json` is used.
2. **Clean the column**: crop it, remove the frame and the grid lines printed across it, and measure the size at
   which the pattern repeats. Every window and threshold later scales with that size, so small and large scans
   behave the same.
3. **Read the legend**: find every swatch (the small sample box) and read its name. Several readers compete and the
   one whose names look most like real rock names wins:
   - two Tesseract readers: boxes of equal size, or the words first and then the box next to each word
   - Pau's **grid** reader (RapidOCR): splits the legend into non-overlapping cells, so a name can never drift into
     its neighbour's
4. **Segment**:
   - **coloured logs**: every pixel row's fill colour is compared with the legend colours (CIE Lab, ΔE < 10). Swatches
     sharing a colour are told apart by their texture.
   - **black-and-white logs**: sliding windows are described by stroke directions, blobs and ink density, then
     clustered with k-means (k chosen by silhouette score). A guard removes short "transition" clusters.

   Boundaries are snapped to the exact pixel row.
5. **Cluster and match**: intervals that look alike share a cluster. Each cluster takes the name of the most similar
   swatch, which is compared at several sizes and stroke weights. Identical swatches are flagged as *ambiguous*.
6. **Convert**: the depth numbers are read with Tesseract in strips (RapidOCR as fallback), then fitted with a
   robust line: RANSAC, outlier trimming, and a piecewise model when the scale changes. This runs in parallel with
   steps 2–5.
7. **Output**: neighbouring intervals with the same rock are merged. Then the CSV, painted previews, legend and
   depth plots and the full-resolution annotated TIF are written.

Older methods are kept for comparison on the Evaluation page:
- `cv`: clustering only
- `hybrid`, `hybrid_v2`, `hybrid_v3`: successive improvements
- `lithology`: Pau's package on its own
- `lithology_cv`: the same as `automatic_lithology_cv`, with the boxes from the `.json`

### 2. YOLO: finding the three boxes automatically

The bonus task. `bbox-predictor/` trains and `bbox-final/` runs two **YOLO11s** detectors:

- a **general** model for the three regions (`depth_column`, `interpreted_lithology_column`, `lithology_legend`),
  run on tiles of the page at 1536 px;
- a **legend specialist** at 2048 px. Legends are the hardest region: they vary most in size and position. Its
  output is rebuilt from fragments and frame lines, with a retry on local windows when no reliable frame is found.

They were trained on Google Colab (T4 GPU) on the annotated development scans, then exported to **OpenVINO int8**, so
they run on any CPU. On the development benchmark the legend reaches a mean IoU of 0.95 (10/10 legends with
IoU ≥ 0.8). `bbox-predictor/` also contains a classical OpenCV alternative and a hybrid YOLO + lines backend, used as
baselines. The detector can run in-process (`bbox-final/predict.py`) or as a small HTTP service
(`bbox-final/server.py`), so the heavy models can live on another machine.

### 3. Lithology Console: run, check and compare

`cv_approach_1/webapp/` is a local web app (FastAPI + plain JavaScript) around the pipeline:

- **New run**: one image, a whole dataset folder (nothing is copied), or an **Evaluation** of several methods
  against ground truth.
- **Pre-flight check**: every image, `.json` and ground truth is validated before starting, with name-mismatch
  detection.
- **Automatic bounding boxes**: a switch to use the detector even when a `.json` is uploaded. The `.json` is then
  the fallback.
- **Settings**: every pipeline parameter, with presets.
- **Live monitor**: progress, stage, ETA, memory and cancel.
- **Results**: accuracy, boundary error, confusion matrix, zoomable page and column viewers, the detected legend
  next to the original one, and the depth fit.
- **Downloads**: CSV + annotated TIF per image.
- **How it works**: a page explaining the method in plain language.

### 4. Processing API and a Drive-style document explorer

`lithology/` (with `webui/`) is the production side: an HTTP API that takes a scan and returns the CSV and the
annotated TIF.

- **Storage**: its `/api/v1` file API stores every upload, its status and its results in **PostgreSQL**, behind a
  processing queue that survives restarts.
- **Bounding boxes**: these are found by the YOLO service.
- **Unity explorer**: on top of the API, the **Document Explorer** works like Google Drive. You upload several TIFFs
  at once, watch their processing status live, search the library, select with click / Shift / Ctrl, and right-click
  to download or delete. Double-clicking a document opens it in the 3D viewer.

### 5. Unity: visualising the results in 3D

`unity-project/` (Unity 6, URP) turns a result into a **3D borehole**:

- the scanned document on one side, and on the other a cylinder whose sections are the extracted intervals, inside a
  cut-away block of ground
- both views scroll and zoom together, mapped through the CSV's pixel rows and depths
- clicking a section selects the interval and its row in the CSV table, and the other way round

Every rock type gets a procedural material. A catalogue of 180 lithology identities is built from 25 base families
and 53 modifiers ("shaly", "calcareous", …), so even unusual legend names get a sensible look.


## Repository layout

| Folder | What it is |
|---|---|
| `cv_approach_1/` | the computer-vision pipeline (`lithology_pipeline.py` + `methods/`) and the Lithology Console web app |
| `lithology/` | Pau's extraction package: RapidOCR legend/depth readers, drawing, HTTP + file API with Postgres ([its README](lithology/README.md)) |
| `webui/` | browser pages for the `lithology/` API (upload, legend review) |
| `bbox-predictor/` | YOLO11 training, evaluation and the classical OpenCV baselines |
| `bbox-final/` | the packaged bounding-box detector (int8 OpenVINO models, CLI and HTTP service) |
| `unity-project/` | Unity document explorer and 3D borehole visualizer |


## Results

- **Example log** (organisers' ground truth): **99.6 %** of pixel rows get the right rock, with a mean boundary
  error of **1.3 px**, fully automatic (boxes found by YOLO).
- **Bounding boxes**: the detected columns overlap the organisers' boxes with an IoU of 0.90–0.95.
- **Runtime**, CPU only (2-core laptop, no GPU). The default method favours accuracy over speed:
  - the boxes take 35–75 s per page, and up to about 8 minutes for a very long mud log (Callaway, a PDF)
  - the extraction is under 10 s for the column, segmentation and depth; the most careful legend reader
    (Pau's grid) adds 1–6 minutes
  - with the organisers' boxes and the fast legend readers only (method `hybrid_v3`), a log takes 10–25 s
- **Memory**: huge scans are read in strips and tiles, so memory stays low even on 30 000-pixel-tall TIFFs.


## Challenges we ran into

- **Every log is different.** Colour or black-and-white, upright or sideways depth labels, legends as grids or
  strips, scans with 1-bit noise. Everything had to generalise from about 50 development images, with hidden
  evaluation images at the end.
- **Legends are the bottleneck.** OCR glues neighbouring names together, reads hatch patterns as letters
  ("CCCCCCEMENT") and misses small swatches. We ended up with several readers competing on a name-quality score.
- **Patterns that look identical.** Some legends draw two rocks with almost the same pattern. These are flagged as
  ambiguous instead of guessed silently.
- **White rocks.** Chalk is white, like the paper, so "no ink" could not simply mean "empty". We had to combine ink
  and paper colour.
- **Huge files.** Scans of tens of thousands of pixels, and PDFs that must be rendered at the right size so the
  pixel rows match the annotations.
- **Speed without losing accuracy.** Tesseract settings, reading depth in strips, OCR in a parallel thread and
  drawing outputs only on demand took our 3-log test set from 202 s to 69 s, with identical results.
- **Integrating four parts during a hackathon.** The CV pipeline, YOLO, the API and Unity all had to agree on one JSON and
  CSV format.


## Project Setup

Python 3.10–3.12, Linux or macOS (Windows: see `run_windows.bat`). Nothing is installed system-wide: every part
has its own virtual environment.

**1. Lithology Console (computer-vision pipeline + web app)**

```bash
cd cv_approach_1
./run.sh                       # first time: creates .venv, a private Tesseract and the packages
# open http://localhost:8000
```

**2. Automatic bounding boxes (YOLO, optional but needed for the bonus)**

```bash
cd cv_approach_1
./setup_auto_boxes.sh          # one time: creates bbox-final/.venv (CPU PyTorch, Ultralytics, OpenVINO), ~1.5 GB
```

The web app then uses it when *Automatic bounding boxes* is on (the default).

**3. Command line**

```bash
cd cv_approach_1
.venv/bin/python lithology_pipeline.py <dataset folder> --labels labels --out outputs
.venv/bin/python -m pytest tests          # regression tests on the example log
```

**4. Processing API with Postgres (for the Unity explorer)**

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env           # set DATABASE_URL
.venv/bin/python -m lithology serve --ui webui --port 8000
bbox-final/run_server.sh       # bounding-box service on port 8100; then set LITHO_BBOX_URL=http://<ip>:8100
```

All endpoints are documented in [lithology/README.md](lithology/README.md).

**5. Unity**

Open `unity-project/` with Unity **6000.5.9f1**, then:
- **Tools → Lithology → Open Document Explorer**: the Drive-style library. Set the API URL in its component.
- **Tools → Lithology → Open CSV Borehole Visualizer**: the 3D viewer, with local CSV and image files.


## Learning 🎓

- **Computer vision without deep learning**: texture descriptors, clustering, colour spaces and robust fitting
  (RANSAC). Often simpler and more explainable than a neural network, and it runs anywhere.
- **Training and shipping YOLO models**: labelling, tiling huge images, training on Colab and int8 quantisation with
  OpenVINO for CPU inference.
- **OCR in practice**: comparing Tesseract and RapidOCR, and how much pre-processing and orientation matter.
- **Building APIs and a database-backed processing queue** with FastAPI and PostgreSQL.
- **Unity 6**: procedural materials, URP and UI Toolkit, used for data visualisation rather than games.
- **Measuring before optimising**: a regression test on the labelled log let us change the pipeline quickly without
  breaking it.
