"""
The extraction methods. lithology_pipeline.py (one level up) is the entry point: it picks one by Config.method.

    lithology_cv_pipeline.py  "automatic_lithology_cv"  (default) lithology_cv + automatic bounding boxes (auto_boxes.py)
                              "lithology_cv"            hybrid_v3 + Pau's grid legend reader, boxes from the .json
    auto_boxes.py                                       runs the YOLO box detector of bbox-final/ on one scan
    hybrid_v3.py              "hybrid_v3"               hybrid_v2 + Pau's robust depth fit, faster depth OCR
    hybrid_v2.py              "hybrid_v2"               colour mode, blank fix, legend quality score (the shared engine
                                                        that v3 and lithology_cv plug into)
    hybrid_pipeline.py        "hybrid"                  first hybrid: cv segmentation + lithology/ OCR fallbacks
    cv_pipeline.py            "cv"                      classic computer vision (clustering); also the shared building
                                                        blocks: ink masks, texture features, rendering, metrics
The "lithology" method is Pau's lithology/ package on its own (called from lithology_pipeline.py).
    lithology2.py             "lithology2"              the "lithology" method + clean-up of its intervals: shape
                                                        fingerprint naming on B/W logs, one name per look-alike group,
                                                        noise -> previous/next name (else blank), no "?"
"""
