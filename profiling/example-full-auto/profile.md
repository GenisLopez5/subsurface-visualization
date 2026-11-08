# End-to-end pipeline profile

- Input: `/home/jaume/CODE/subsurface-visualization/bbox-predictor/hack27-20261107T141707Z-1-001/hack27/project/example`
- Method: `automatic_lithology_cv`
- Documents: **1** completed / **1** requested
- Total wall time: **19.53 s**
- Median per document: **19.47 s**
- P90 per document: **19.47 s**
- Peak process-tree RSS: **929.9 MB**
- Hardware: **Intel(R) Core(TM) i5-8350U CPU @ 1.70GHz**, 4 cores / 8 threads

## Stage breakdown

| Stage | Mean | Median | P90 | Share |
|---|---:|---:|---:|---:|
| Column + legend detection | 0.20 s | 0.20 s | 0.20 s | 1.0% |
| Column preprocessing | 0.19 s | 0.19 s | 0.19 s | 1.0% |
| Legend extraction + OCR | 15.07 s | 15.07 s | 15.07 s | 77.4% |
| Subsurface prediction | 2.23 s | 2.23 s | 2.23 s | 11.4% |
| Depth OCR + calibration | 0.00 s | 0.00 s | 0.00 s | 0.0% |
| OpenCV rendering + outputs | 1.79 s | 1.79 s | 1.79 s | 9.2% |

> Depth OCR runs concurrently with column, legend and segmentation work. Its stage value is the remaining wait, so the stage rows partition wall time instead of double-counting parallel work.

> Peak RSS samples the profiler, detector and OCR child processes every 50 ms. Very short peaks may be missed.
