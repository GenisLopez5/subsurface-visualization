#!/usr/bin/env python3
"""Prepare band crops, then compare 1024/1536 refiners sequentially on Colab."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bbox_detector.common import write_json
from bbox_detector.legend_data import prepare_legend_dataset
from bbox_detector.legend_experiments import train_band_experiments


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", default="artifacts/legend_bands")
    parser.add_argument("--base-predictions")
    parser.add_argument("--specialist-predictions")
    parser.add_argument("--policy")
    parser.add_argument("--model", default="yolo11s.pt")
    parser.add_argument("--device", default="0")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args(argv)
    if not args.prepare_only and not all((args.base_predictions, args.specialist_predictions, args.policy)):
        parser.error("Training needs --base-predictions, --specialist-predictions and --policy")
    output = Path(args.output)
    dataset = prepare_legend_dataset(args.manifest, output/"dataset", imgsz=1536,
                                     train_variants=4, crop_mode="band")
    status = {"dataset_summary": dataset["summary"], "status": "prepared",
              "runs": [{"imgsz": resolution, "epochs": 60, "patience": 10, "batch": 4,
                        "rect": True, "seed": 42, "optimizer": "AdamW", "lr0": .001}
                       for resolution in (1024, 1536)]}
    write_json(output/"experiment_status.json", status)
    if not args.prepare_only:
        if args.device != "cpu":
            import torch
            if not torch.cuda.is_available():
                status.update(status="gpu_unavailable", reason="Run on a Colab GPU, or explicitly choose --device cpu")
                write_json(output/"experiment_status.json", status)
                parser.exit(2, "No CUDA GPU available. Dataset and experiment configuration were saved.\n")
        result = train_band_experiments(output/"dataset/dataset.yaml", args.manifest,
                                        args.base_predictions, args.specialist_predictions,
                                        args.policy, output/"runs", device=args.device, model=args.model)
        status.update(status="completed", comparison=result)
        write_json(output/"experiment_status.json", status)
    print(json.dumps(status, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
