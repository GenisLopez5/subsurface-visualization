"""Build a CPU-only classical registration bank from training documents."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bbox_detector.classical import build_template_bank


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", default="artifacts/templates")
    parser.add_argument("--work-width", type=int, default=1024)
    parser.add_argument("--max-features", type=int, default=3000)
    args = parser.parse_args()
    bank = build_template_bank(args.manifest, args.output, work_width=args.work_width, max_features=args.max_features)
    print(f"Built {len(bank['references'])} training references in {args.output}/bank.json")


if __name__ == "__main__":
    main()
