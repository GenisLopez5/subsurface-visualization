#!/usr/bin/env python3
"""Create a bounded legend gallery for visual label review; never edit labels."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image, ImageDraw, ImageOps
from bbox_detector.common import load_annotation, open_scan, read_crop, write_json
from bbox_detector.data import prepare_dataset


def audit_legends(dataset, output):
    output = Path(output)
    manifest = prepare_dataset(dataset, output / "audit", materialize=False)
    (output / "gallery").mkdir(parents=True, exist_ok=True)
    rows, cells, sheets = [], [], []
    for index, record in enumerate(manifest["records"]):
        annotation = load_annotation(record["annotation_path"])
        box = annotation["bounding_boxes"]["lithology_legend"]
        pad = max(12, round((box[3]-box[1]) * .08))
        region = (max(0, box[0]-pad), max(0, box[1]-pad),
                  min(record["width"], box[2]+pad), min(record["height"], box[3]+pad))
        scale = min(1, 1600/(region[2]-region[0]), 1000/(region[3]-region[1]))
        size = (max(1, round((region[2]-region[0])*scale)), max(1, round((region[3]-region[1])*scale)))
        with open_scan(record["image_path"]) as scan:
            with read_crop(scan, region, size=size) as crop:
                sx, sy = crop.width/(region[2]-region[0]), crop.height/(region[3]-region[1])
                ImageDraw.Draw(crop).rectangle(((box[0]-region[0])*sx, (box[1]-region[1])*sy,
                                               (box[2]-region[0])*sx-1, (box[3]-region[1])*sy-1), outline="#00984a", width=2)
                crop.save(output / "gallery" / f"{index:02d}.jpg", quality=94)
                cell = Image.new("RGB", (800, 360), "white")
                thumb = ImageOps.contain(crop, (790, 320))
                cell.paste(thumb, ((800-thumb.width)//2, 35))
                thumb.close()
        ImageDraw.Draw(cell).text((8, 5), f"{index:02d} {record['image_name'][:88]}", fill="black")
        cells.append(cell)
        rows.append({"index": index, "image_name": record["image_name"], "group": record["group"],
                     "split": record["split"], "legend_box": box,
                     "annotation_sha256": hashlib.sha256(Path(record["annotation_path"]).read_bytes()).hexdigest(),
                     "preview": f"gallery/{index:02d}.jpg", "review_status": "pending",
                     "layout": None, "frame": None, "notes": ""})
        if len(cells) == 8 or index == len(manifest["records"])-1:
            sheet = Image.new("RGB", (1600, 360*((len(cells)+1)//2)), "white")
            for i, cell in enumerate(cells):
                sheet.paste(cell, ((i%2)*800, (i//2)*360))
                cell.close()
            target = f"contact_{len(sheets):02d}.jpg"
            sheet.save(output / target, quality=93)
            sheet.close()
            sheets.append(target)
            cells = []
    write_json(output / "review.json", {"note": "Visual review only; original annotations are unchanged", "documents": rows})
    content = "<html><meta charset='utf-8'><title>Legend annotation audit</title><body>"
    for row in rows:
        content += f"<h3>{row['index']:02d} {html.escape(row['image_name'])}</h3><img style='max-width:100%' src='{row['preview']}'><hr>"
    (output / "index.html").write_text(content + "</body></html>")
    return {"documents": len(rows), "manifest": str(output / "audit/manifest.json"), "contact_sheets": sheets}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=None)
    parser.add_argument("--output", default="artifacts/legend_optimization")
    args = parser.parse_args(argv)
    print(json.dumps(audit_legends(args.dataset, args.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
