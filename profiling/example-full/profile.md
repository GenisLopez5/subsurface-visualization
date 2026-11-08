# End-to-end pipeline profile

- Input: `/home/jaume/CODE/subsurface-visualization/bbox-predictor/hack27-20261107T141707Z-1-001/hack27/project/example`
- Method: `automatic_lithology_cv`
- Documents: **1** completed / **1** requested
- Total wall time: **18.82 s**
- Median per document: **18.70 s**
- P90 per document: **18.70 s**
- Peak process-tree RSS: **978.5 MB**
- Hardware: **Intel(R) Core(TM) i5-8350U CPU @ 1.70GHz**, 4 cores / 8 threads

## Stage breakdown

| Stage | Mean | Median | P90 | Share |
|---|---:|---:|---:|---:|
| Column + legend detection | 0.23 s | 0.23 s | 0.23 s | 1.2% |
| Column preprocessing | 0.23 s | 0.23 s | 0.23 s | 1.2% |
| Legend extraction + OCR | 14.88 s | 14.88 s | 14.88 s | 79.6% |
| Subsurface prediction | 1.73 s | 1.73 s | 1.73 s | 9.3% |
| Depth OCR + calibration | 0.00 s | 0.00 s | 0.00 s | 0.0% |
| OpenCV rendering + outputs | 1.63 s | 1.63 s | 1.63 s | 8.7% |

> Depth OCR runs concurrently with column, legend and segmentation work. Its stage value is the remaining wait, so the stage rows partition wall time instead of double-counting parallel work.

> Peak RSS samples the profiler, detector and OCR child processes every 50 ms. Very short peaks may be missed.
