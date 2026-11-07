"""Prepare document-aware train/validation crops for YOLO."""

import argparse
import json

from bbox_detector.data import prepare_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=None, help="Root containing images/ and bbox_annotation/")
    parser.add_argument("--output", default="artifacts/dataset")
    parser.add_argument("--imgsz", type=int, default=1536)
    parser.add_argument("--max-tiles", type=int, default=16)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--val-fraction", type=float, default=0.2)
    parser.add_argument("--audit-only", action="store_true", help="Validate headers, split, and write manifest without decoding crops")
    args = parser.parse_args()
    manifest = prepare_dataset(args.dataset, args.output, args.imgsz, args.max_tiles, args.seed, args.val_fraction, not args.audit_only)
    print(json.dumps(manifest["summary"], indent=2))
    print(f"Manifest: {manifest['output_root']}/manifest.json")
    print(f"YOLO config: {manifest['output_root']}/dataset.yaml")


if __name__ == "__main__":
    main()
