"""
The extraction methods. lithology_pipeline.py (one level up) is the entry point: it picks one by Config.method.

    lithology_cv_pipeline.py  "lithology_cv" (default)  hybrid_v3 + Pau's grid legend parser
    hybrid_v3.py              "hybrid_v3"               hybrid_v2 + Pau's robust depth fit, faster depth OCR
    hybrid_v2.py              "hybrid_v2"               colour mode, blank fix, legend quality score (the shared engine
                                                        that v3 and lithology_cv plug into)
    hybrid_pipeline.py        "hybrid"                  first hybrid: cv segmentation + lithology/ OCR fallbacks
    cv_pipeline.py            "cv"                      classic computer vision (clustering); also the shared building
                                                        blocks: ink masks, texture features, rendering, metrics
The "lithology" method is Pau's lithology/ package on its own (called from lithology_pipeline.py).
"""
