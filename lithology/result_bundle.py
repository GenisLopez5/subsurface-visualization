"""Optional, unannotated source image for document viewer result bundles."""
from pathlib import Path
from zipfile import ZIP_STORED


def write_source_image(archive, original, fallback_name, stem):
    if not original:
        return False
    extension = Path(original.get("filename") or fallback_name).suffix.lower()
    if extension not in (".tif", ".tiff", ".png", ".jpg", ".jpeg"):
        return False
    archive.writestr(f"{stem}_source{extension}", bytes(original["data"]), compress_type=ZIP_STORED)
    return True
