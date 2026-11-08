"""
Method "lithology2" = method "lithology" + lithology/refine.py (the clean-up now lives in the main package, where
extract.extract() runs it by default). Kept so lithology_pipeline.py can call it with its own Config.
"""
from lithology.refine import CATS, refine, shape_fingerprint  # noqa: F401
