"""
Drawing functions from the challenge's example_notebook.ipynb, copied verbatim
(imports, constants and function definitions only; the notebook's demo code is left out).

Do not edit by hand: regenerate from the notebook so the drawing stays identical to it.
The main entry point is `visualize_results(image, bboxes, results, scale, save_path, coverage, colors)`.
"""
# fmt: off
import colorsys

import csv

import json

from pathlib import Path

import numpy as np

from PIL import Image, ImageDraw, ImageFont

Image.MAX_IMAGE_PIXELS = None

def get_font(size):
    # DejaVu ships with matplotlib/most Linux installs; fall back to Pillow's built-in font otherwise
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()

def crop_rgb(img, box, scale=1.0, strip=2000):
    """Crop box=(x0, y0, x1, y1) out of img as RGB, optionally shrunk by `scale`.
    The crop is converted and shrunk `strip` rows at a time, so even a 1-bit image that is
    100,000+ px tall never has to exist at full size as RGB."""
    x0, y0, x1, y1 = box
    out = Image.new("RGB", (round((x1 - x0) * scale), round((y1 - y0) * scale)))
    for ys in range(y0, y1, strip):
        ye = min(ys + strip, y1)
        part = img.crop((x0, ys, x1, ye)).convert("RGB")
        top, bottom = round((ys - y0) * scale), round((ye - y0) * scale)
        if scale != 1.0:
            part = part.resize((out.width, max(1, bottom - top)), Image.Resampling.BOX)
        out.paste(part, (0, top))
    return out

BOX_COLORS = {
    "depth_column": (255, 0, 0),
    "interpreted_lithology_column": (0, 160, 255),
    "lithology_legend": (255, 130, 0),
}

def draw_boxes(img, boxes, origin=(0, 0), scale=1.0, line_width=4, font_size=16):
    """Draw boxes (given in full-image pixels) on img, a crop that starts at `origin` and was scaled by `scale`."""
    draw = ImageDraw.Draw(img)
    font = get_font(font_size)
    for i, (name, (x0, y0, x1, y1)) in enumerate(boxes.items()):
        color = BOX_COLORS.get(name, (0, 200, 0))
        rect = [(x0 - origin[0]) * scale, (y0 - origin[1]) * scale, (x1 - origin[0]) * scale - 1, (y1 - origin[1]) * scale - 1]
        if rect[3] < 0 or rect[1] > img.height or rect[2] < 0 or rect[0] > img.width:
            continue  # this box lies outside the crop
        draw.rectangle(rect, outline=color, width=line_width)
        draw.text((rect[0] + 6, max(rect[1], 0) + 4 + (font_size + 4) * i), name, fill=color, font=font)
    return img

def load_results(path):
    """Read a results CSV (top_depth, bottom_depth, lithology, top_y, bottom_y, and optionally cluster_id) into a list of dicts."""
    with open(path, newline="") as f:
        return [
            {"top_depth": float(r["top_depth"]), "bottom_depth": float(r["bottom_depth"]), "lithology": r["lithology"],
             "top_y": int(r["top_y"]), "bottom_y": int(r["bottom_y"]),
             "cluster_id": r.get("cluster_id") or None}  # cluster_id is optional
            for r in csv.DictReader(f)
        ]

PALETTE = [(40, 160, 60), (160, 100, 40), (240, 190, 0), (150, 60, 170), (0, 150, 150), (230, 110, 20),
           (200, 60, 120), (110, 110, 110)]

LINE_COLOR = (0, 40, 200)

def build_color_map(labels):
    """Give every distinct label its own colour, in order of first appearance. Works for any labels:
    the palette is used first, then extra hues are generated, so nothing is hard-coded per lithology."""
    colors = {}
    for label in labels:
        if label in colors:
            continue
        i = len(colors)
        if i < len(PALETTE):
            colors[label] = PALETTE[i]
        else:  # beyond the palette: spread hues with the golden ratio so neighbours stay distinct
            h = (i * 0.618034) % 1.0
            colors[label] = tuple(int(255 * c) for c in colorsys.hsv_to_rgb(h, 0.65, 0.8))
    return colors

def overlay_results(img, results, colors, column_box, depth_box, origin, scale, alpha=150, coverage=0.5):
    """Paint every interval over the left `coverage` of the lithology column on `img` (a crop starting at
    `origin`, shrunk by `scale`) in the colour given for its label in `colors`, so the original column stays visible on the right. At every interval
    boundary a thin line runs from the depth column to the lithology column and the boundary depth is written
    to the left of the depth column, and the interval's cluster id (if it has one) to the right of the lithology
    column. Every row of `results` is drawn, however small the interval."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = get_font(13)
    x0, x1 = (column_box[0] - origin[0]) * scale, (column_box[2] - origin[0]) * scale
    xm = x0 + (x1 - x0) * coverage  # right edge of the painted part
    xd = (depth_box[0] - origin[0]) * scale  # left edge of the depth column
    boundaries = {}  # y (full-image pixels) -> depths given at that boundary
    for r in results:
        y0, y1 = (r["top_y"] - origin[1]) * scale, (r["bottom_y"] - origin[1]) * scale
        color = colors[r["lithology"]]
        draw.rectangle([x0, y0, xm, y1], fill=color + (alpha,), outline=color + (255,), width=1)
        if r.get("cluster_id") is not None:
            draw.text((x1 + 8, (y0 + y1) / 2), f"cluster {r['cluster_id']}", fill=color + (255,), font=font, anchor="lm",
                      stroke_width=3, stroke_fill=(255, 255, 255, 255))
        boundaries.setdefault(r["top_y"], []).append(r["top_depth"])
        boundaries.setdefault(r["bottom_y"], []).append(r["bottom_depth"])
    for y, depths in boundaries.items():
        yy = (y - origin[1]) * scale
        draw.line([(xd, yy), (x1, yy)], fill=LINE_COLOR + (255,), width=1)
        label = " / ".join(f"{d:g}" for d in dict.fromkeys(depths))  # more than one value only if the depths jump
        draw.text((xd - 6, yy), label, fill=LINE_COLOR + (255,), font=font, anchor="rm",
                  stroke_width=3, stroke_fill=(255, 255, 255, 255))
    print(f"drew {len(results)} intervals and {len(boundaries)} boundary labels")
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

def add_legend(img, colors, clusters=None, row_height=34):
    """Return img with a white bar on top holding one colour swatch per label (wrapping onto more rows if needed).
    `clusters` ({label: [cluster ids]}) adds the cluster ids found for each label next to its name."""
    clusters = clusters or {}
    font = get_font(15)
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    slots, x, row = [], 10, 0
    texts = {name: name + (f"  (cluster {', '.join(clusters[name])})" if clusters.get(name) else "") for name in colors}
    for name in colors:
        w = 26 + int(measure.textlength(texts[name], font=font)) + 20
        if x + w > img.width and x > 10:  # no room left on this row: start a new one
            x, row = 10, row + 1
        slots.append((name, x, row))
        x += w
    bar = row_height * (row + 1)
    out = Image.new("RGB", (img.width, img.height + bar), (255, 255, 255))
    out.paste(img, (0, bar))
    draw = ImageDraw.Draw(out)
    for name, x, row in slots:
        y = row * row_height
        draw.rectangle([x, y + 8, x + 18, y + 26], fill=colors[name], outline=(0, 0, 0))
        draw.text((x + 26, y + 17), texts[name], fill=(0, 0, 0), font=font, anchor="lm")
    return out

def visualize_results(image, bboxes, results, scale=0.25, save_path=None, coverage=0.5, colors=None):
    """Plot the results CSV on the whole log image: coloured intervals over the lithology column, boundary lines and
    depths next to the depth column, cluster ids next to the lithology column, and a colour key on top.
    Nothing is cropped. `scale` shrinks the picture (1.0 = original size). Returns a PIL image."""
    if not isinstance(image, Image.Image):
        image = Image.open(image)
    if not isinstance(bboxes, dict):
        bboxes = json.loads(Path(bboxes).read_text())
    if not isinstance(results, list):
        results = load_results(results)
    boxes = bboxes.get("bounding_boxes", bboxes)  # accept the whole annotation file or just its boxes
    if colors is None:
        colors = build_color_map(r["lithology"] for r in results)

    view = crop_rgb(image, (0, 0, image.width, image.height), scale)
    view = draw_boxes(view, boxes, scale=scale, line_width=2, font_size=12)
    view = overlay_results(view, results, colors, boxes["interpreted_lithology_column"], boxes["depth_column"],
                           (0, 0), scale, coverage=coverage)
    clusters = {}  # label -> cluster ids seen for it, in order of first appearance
    for r in results:
        if r.get("cluster_id") is not None:
            clusters.setdefault(r["lithology"], [])
            if r["cluster_id"] not in clusters[r["lithology"]]:
                clusters[r["lithology"]].append(r["cluster_id"])
    view = add_legend(view, colors, clusters)
    if save_path is not None:
        view.save(save_path)
    return view
