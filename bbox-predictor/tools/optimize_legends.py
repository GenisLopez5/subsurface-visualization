#!/usr/bin/env python3
"""Benchmark the bounded fragment grid and frame recovery without retraining."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bbox_detector.legend_benchmark import benchmark_legend_policies


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--base-predictions", required=True)
    parser.add_argument("--specialist-predictions", required=True)
    parser.add_argument("--output", default="artifacts/legend_optimization/benchmark")
    parser.add_argument("--formats", default=None)
    parser.add_argument("--no-previews", action="store_true")
    args = parser.parse_args(argv)
    result = benchmark_legend_policies(args.manifest, args.base_predictions, args.specialist_predictions,
                                      args.output, formats_path=args.formats, save_previews=not args.no_previews)
    print(json.dumps({"selected_strategy": result["selected_strategy"], "selected": result["selected"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
