#!/usr/bin/env python3
"""Package the runnable source and annotated source TIFFs for Google Drive/Colab."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bbox_detector.common import find_dataset


SOURCE_FILES = (
    "README.md", "docs/classical.md", "docs/legend.md", "docs/legend_optimization.md", "docs/legend_review.json", "docs/legend_policy.json", "requirements.txt", "requirements-cv.txt", "requirements-dev.txt", "pytest.ini",
    "prepare_dataset.py", "train_bboxes.py", "predict_bboxes.py", "evaluate_bboxes.py",
    "tools/package_colab.py", "tools/build_templates.py", "tools/benchmark_bboxes.py", "tools/train_legend.py", "tools/evaluate_legend.py",
    "tools/audit_legends.py", "tools/optimize_legends.py", "tools/train_legend_bands.py",
    "notebooks/train_colab.ipynb", "notebooks/classical_colab.ipynb", "notebooks/legend_colab.ipynb", "notebooks/legend_optimize_colab.ipynb",
)


def package_colab(dataset: str | Path | None, output: str | Path, *, include_data: bool = True) -> Path:
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    sources = [(ROOT / name, name) for name in SOURCE_FILES]
    sources += [(p, str(p.relative_to(ROOT))) for directory in ("bbox_detector", "tests") for p in sorted((ROOT / directory).glob("*.py"))]
    if include_data:
        import json
        dataset = find_dataset(dataset)
        for annotation in sorted((dataset / "bbox_annotation").glob("*.json")):
            data = json.loads(annotation.read_text())
            image_name = data["image_name"]
            if Path(image_name).name != image_name:
                raise ValueError(f"Invalid annotation image name: {image_name}")
            sources.extend(((annotation, f"dataset/bbox_annotation/{annotation.name}"),
                            (dataset / "images" / image_name, f"dataset/images/{image_name}")))
    missing = [str(path) for path, _ in sources if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Bundle sources are missing: {missing}")
    temporary = output.with_suffix(output.suffix + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", allowZip64=True) as archive:
            for source, name in sources:
                # TIFF input is already compressed; avoid costly recompression of ~1 GB.
                compression = zipfile.ZIP_STORED if source.suffix.lower() in {".tif", ".tiff"} else zipfile.ZIP_DEFLATED
                archive.write(source, name, compress_type=compression)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset")
    parser.add_argument("--output", default="artifacts/colab_bundle.zip")
    parser.add_argument("--source-only", action="store_true", help="Package code only; supply dataset separately in Colab")
    args = parser.parse_args(argv)
    result = package_colab(args.dataset, args.output, include_data=not args.source_only)
    print(f"{result} ({result.stat().st_size / 1024**2:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
