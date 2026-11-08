# End-to-end pipeline profile

- Input: `/home/jaume/CODE/subsurface-visualization/bbox-predictor/hack27-20261107T141707Z-1-001/hack27/project/example`
- Method: `automatic_lithology_cv`
- Documents: **1** completed / **1** requested
- Total wall time: **35.42 s**
- Median per document: **35.38 s**
- P90 per document: **35.38 s**
- Peak process-tree RSS: **2216.7 MB**
- Hardware: **Intel(R) Core(TM) i5-8350U CPU @ 1.70GHz**, 4 cores / 8 threads

## Stage breakdown

| Stage | Mean | Median | P90 | Share |
|---|---:|---:|---:|---:|
| Column + legend detection | 8.54 s | 8.54 s | 8.54 s | 24.1% |
| Column preprocessing | 0.26 s | 0.26 s | 0.26 s | 0.7% |
| Legend extraction + OCR | 23.09 s | 23.09 s | 23.09 s | 65.3% |
| Subsurface prediction | 1.75 s | 1.75 s | 1.75 s | 5.0% |
| Depth OCR + calibration | 0.00 s | 0.00 s | 0.00 s | 0.0% |
| OpenCV rendering + outputs | 1.73 s | 1.73 s | 1.73 s | 4.9% |

> Depth OCR runs concurrently with column, legend and segmentation work. Its stage value is the remaining wait, so the stage rows partition wall time instead of double-counting parallel work.

> Peak RSS samples the profiler, detector and OCR child processes every 50 ms. Very short peaks may be missed.
