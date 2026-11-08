# End-to-end pipeline profile

- Input: `/home/jaume/CODE/subsurface-visualization/bbox-predictor/hack27-20261107T141707Z-1-001/hack27/project/example`
- Method: `automatic_lithology_cv`
- Documents: **1** completed / **1** requested
- Total wall time: **33.93 s**
- Median per document: **33.91 s**
- P90 per document: **33.91 s**
- Peak process-tree RSS: **2220.6 MB**
- Hardware: **Intel(R) Core(TM) i5-8350U CPU @ 1.70GHz**, 4 cores / 8 threads

## Stage breakdown

| Stage | Mean | Median | P90 | Share |
|---|---:|---:|---:|---:|
| Column + legend detection | 7.60 s | 7.60 s | 7.60 s | 22.4% |
| Column preprocessing | 0.18 s | 0.18 s | 0.18 s | 0.5% |
| Legend extraction + OCR | 22.85 s | 22.85 s | 22.85 s | 67.4% |
| Subsurface prediction | 1.76 s | 1.76 s | 1.76 s | 5.2% |
| Depth OCR + calibration | 0.00 s | 0.00 s | 0.00 s | 0.0% |
| OpenCV rendering + outputs | 1.52 s | 1.52 s | 1.52 s | 4.5% |

## Detection breakdown

| Detector step | Mean | Share of detection |
|---|---:|---:|
| Model startup + orchestration | 3.39 s | 44.6% |
| General column model | 2.35 s | 30.9% |
| Legend specialist + geometry | 1.86 s | 24.4% |

## Legend extraction breakdown

| Legend step | Mean | Share of legend stage |
|---|---:|---:|
| Tesseract size groups | 1.25 s | 5.5% |
| Tesseract text anchored | 2.36 s | 10.3% |
| RapidOCR grid reader | 19.19 s | 84.0% |
| Crop + swatch processing | 0.05 s | 0.2% |

> Depth OCR runs concurrently with column, legend and segmentation work. Its stage value is the remaining wait, so the stage rows partition wall time instead of double-counting parallel work.

> Peak RSS samples the profiler, detector and OCR child processes every 50 ms. Very short peaks may be missed.
