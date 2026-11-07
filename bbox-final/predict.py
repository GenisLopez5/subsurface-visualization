#!/usr/bin/env python3
"""Predict the two columns and legend using the packaged final workflow."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys

from bbox_runtime.common import write_json
from bbox_runtime.workflow import BoundingBoxPredictor, ROOT


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="Original TIFF, PNG or JPEG")
    parser.add_argument("--output", type=Path, help="Also save the result to this JSON file")
    parser.add_argument("--device", default="auto", help="auto (default), cpu, or GPU index such as 0")
    parser.add_argument("--diagnostics", type=Path, help="Optional inference details, separate from the result")
    args = parser.parse_args(argv)
    if not args.image.is_file():
        print(f"Prediction failed: image does not exist: {args.image}", file=sys.stderr)
        return 2
    protected = {args.image.resolve(), (ROOT/"pipeline.json").resolve()}
    if (ROOT/"models").is_dir():
        protected.update(path.resolve() for path in (ROOT/"models").iterdir() if path.is_file())
    for path in (args.output, args.diagnostics):
        if path is not None and path.resolve() in protected:
            print("Prediction failed: output paths must differ from input/config/model files", file=sys.stderr)
            return 2
    if args.output and args.diagnostics and args.output.resolve() == args.diagnostics.resolve():
        print("Prediction failed: output and diagnostics must be different files", file=sys.stderr)
        return 2
    details = {}
    try:
        # Third-party logs belong on stderr; stdout is always parseable JSON.
        with redirect_stdout(sys.stderr):
            predictor = BoundingBoxPredictor(device=args.device)
            result = predictor.predict(args.image, diagnostics=details)
        if args.diagnostics:
            write_json(args.diagnostics, details)
        if args.output:
            write_json(args.output, result)
    except (RuntimeError, ValueError, OSError, ImportError, KeyError, TypeError) as error:
        print(f"Prediction failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
