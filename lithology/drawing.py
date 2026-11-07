"""
Drawing results with the notebook's own `visualize_results` (see notebook_viz.py), called the way
the notebook calls it: with *files*. Temporary files in the notebook's formats are written, the
notebook code reads them and saves the picture:

    page image   (the .tif, or a PDF rendered to a temporary .png)
    annotation   (the bounding-box JSON)
    results.csv  (example.csv columns, cluster_id included)
"""
from __future__ import annotations

import contextlib
import csv
import io
import json
import tempfile
from pathlib import Path

from PIL import Image

from . import notebook_viz as NB

Image.MAX_IMAGE_PIXELS = None
MAX_OUTPUT_PIXELS = 80_000_000  # guard: the notebook code builds RGB + RGBA copies at full output size


def write_results_csv(intervals, path, y_shift=0, y_clip=None):
    """Intervals in example.csv format: top_depth,bottom_depth,lithology,top_y,bottom_y,cluster_id.
    cluster_id = one id per distinct lithology, in order of first appearance."""
    ids = {}
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["top_depth", "bottom_depth", "lithology", "top_y", "bottom_y", "cluster_id"])
        for r in intervals:
            cid = ids.setdefault(r["lithology"], len(ids))
            ty, by = r["top_y"] - y_shift, r["bottom_y"] - y_shift
            if y_clip is not None:
                if by <= 0 or ty >= y_clip:
                    continue
                ty, by = max(ty, 0), min(by, y_clip)
            w.writerow([f"{r['top_depth']:g}", f"{r['bottom_depth']:g}", r["lithology"], ty, by, cid])


def default_scale(height: int) -> float:
    """Whole log in one picture of at most ~12k px tall (the notebook's default is 0.25)."""
    return min(0.25, 12000 / height)


def draw(page_path, annotation_path, result, scale, out_dir, page_image=None, y_range=None, tif=False,
         tif_scale=None):
    """Write the temp files, run the notebook's visualize_results on them, return the PNG path
    (and also a high-resolution LZW-compressed .tif next to it when tif=True, see render_tif;
    tif_scale=None means full resolution, or the largest scale under TIF_MAX_PIXELS).

    page_path   an image file the notebook can Image.open (a PDF must be rendered first)
    y_range     (y0, y1): draw only that strip of the page; page, annotation and CSV are
                cropped/shifted into the temp files first
    """
    tmp = Path(tempfile.mkdtemp(prefix="draw_", dir=out_dir))
    colors = NB.build_color_map(r["lithology"] for r in result["intervals"])  # same colours in every view
    page = page_image or Image.open(page_path)
    if y_range is None:
        img_file, ann_file = Path(page_path), Path(annotation_path)
        out_w, out_h = page.width * scale, page.height * scale
        write_results_csv(result["intervals"], tmp / "results.csv")
    else:
        y0, y1 = max(0, int(y_range[0])), min(page.height, int(y_range[1]))
        out_w, out_h = page.width * scale, (y1 - y0) * scale
        img_file = tmp / "page.png"
        page.crop((0, y0, page.width, y1)).save(img_file)
        ann = json.loads(Path(annotation_path).read_text())
        h = y1 - y0
        ann["height"] = h
        ann["bounding_boxes"] = {
            k: [b[0], min(max(b[1] - y0, 0), h), b[2], min(max(b[3] - y0, 0), h)] for k, b in ann["bounding_boxes"].items()
        }
        ann_file = tmp / "annotation.json"
        ann_file.write_text(json.dumps(ann, indent=2))
        write_results_csv(result["intervals"], tmp / "results.csv", y_shift=y0, y_clip=h)
    if out_w * out_h > MAX_OUTPUT_PIXELS:
        raise ValueError(f"output image would be {int(out_w)}x{int(out_h)} px; use a smaller scale")

    out = tmp / "result.png"
    with contextlib.redirect_stdout(io.StringIO()):  # the notebook prints a line per call
        view = NB.visualize_results(str(img_file), str(ann_file), str(tmp / "results.csv"), scale=scale,
                                    save_path=str(out), colors=colors)
    if tif:
        render_tif(page, annotation_path, result, tmp / "result.tif", scale=tif_scale, y_range=y_range, colors=colors)
    return out


# ---------------------------------------------------------------------------------------------
# High-quality TIF: same layout and colours as the notebook's visualize_results, but drawn at
# (near) full page resolution with text, lines and the legend scaled up to match, so it stays
# as sharp as the source scan. The notebook code itself (notebook_viz.py) is left unchanged.

TIF_MAX_PIXELS = 60_000_000  # full resolution unless the log is huge (then the largest scale that fits)
NB_SCALE = 0.25              # the notebook's sizes (fonts, lines, legend) are meant for this scale


_FONTS = ("DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf",
          "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
          "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Bold.ttf",
          "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


def _font(size: int):
    """A scalable font of `size` px (the notebook's get_font falls back to Pillow's fixed ~10 px
    bitmap font when DejaVu isn't installed, which would make the labels tiny at full resolution)."""
    from PIL import ImageFont
    for name in _FONTS:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size)


def tif_scale_for(width: int, height: int) -> float:
    return min(1.0, (TIF_MAX_PIXELS / (width * height)) ** 0.5)


def _intervals_for(result, y_shift=0, y_clip=None):
    """Intervals with cluster ids (as in write_results_csv), shifted/clipped to a strip of the page."""
    ids, out = {}, []
    for r in result["intervals"]:
        cid = ids.setdefault(r["lithology"], len(ids))
        ty, by = r["top_y"] - y_shift, r["bottom_y"] - y_shift
        if y_clip is not None:
            if by <= 0 or ty >= y_clip:
                continue
            ty, by = max(ty, 0), min(by, y_clip)
        out.append({**r, "top_y": ty, "bottom_y": by, "cluster_id": str(cid)})
    return out


def render_tif(page, annotation_path, result, out_path, scale=None, y_range=None, colors=None):
    """Draw the results on the page (or the strip y_range of it) and save an LZW-compressed TIF
    that keeps the source DPI (adjusted for the scale). Returns the path."""
    from PIL import ImageDraw

    page = page if isinstance(page, Image.Image) else Image.open(page)
    dpi = page.info.get("dpi")
    boxes = json.loads(Path(annotation_path).read_text())["bounding_boxes"]
    y0, y1 = 0, page.height
    if y_range is not None:
        y0, y1 = max(0, int(y_range[0])), min(page.height, int(y_range[1]))
    scale = scale or tif_scale_for(page.width, y1 - y0)
    if page.width * (y1 - y0) * scale * scale > MAX_OUTPUT_PIXELS:
        raise ValueError(f"output image would be {int(page.width * scale)}x{int((y1 - y0) * scale)} px; "
                         "use a smaller scale")
    results = _intervals_for(result, y0, y1 - y0) if y_range is not None else _intervals_for(result)
    colors = colors or NB.build_color_map(r["lithology"] for r in results)
    k = max(1.0, scale / NB_SCALE)           # size factor for everything the notebook draws in fixed pixels
    px = lambda v: max(1, round(v * k))      # noqa: E731

    img = NB.crop_rgb(page, (0, y0, page.width, y1), scale)
    draw = ImageDraw.Draw(img)
    S = lambda v: v * scale                  # noqa: E731  page px -> output px
    Y = lambda v: (v - y0) * scale           # noqa: E731

    # annotation boxes
    font = _font(px(12))
    for i, (name, (bx0, by0, bx1, by1)) in enumerate(boxes.items()):
        rect = [S(bx0), Y(by0), S(bx1) - 1, Y(by1) - 1]
        if rect[3] < 0 or rect[1] > img.height:
            continue
        color = NB.BOX_COLORS.get(name, (0, 200, 0))
        draw.rectangle(rect, outline=color, width=px(2))
        draw.text((rect[0] + px(6), max(rect[1], 0) + px(4) + px(16) * i), name, fill=color, font=font)

    # intervals: translucent fill over the left half of the column, boundaries + depths, cluster ids
    col, dep = boxes["interpreted_lithology_column"], boxes["depth_column"]
    x0, x1 = S(col[0]), S(col[2])
    xm, xd = x0 + (x1 - x0) * 0.5, S(dep[0])
    for r in results:
        ty, by = Y(r["top_y"] + y0), Y(r["bottom_y"] + y0)
        box = (round(x0), round(ty), round(xm), max(round(ty) + 1, round(by)))
        region = img.crop(box)
        img.paste(Image.blend(region, Image.new("RGB", region.size, colors[r["lithology"]]), 150 / 255), box[:2])
    draw = ImageDraw.Draw(img)
    font = _font(px(13))
    stroke = dict(stroke_width=px(3), stroke_fill=(255, 255, 255))
    boundaries = {}
    for r in results:
        ty, by = Y(r["top_y"] + y0), Y(r["bottom_y"] + y0)
        color = colors[r["lithology"]]
        draw.rectangle([x0, ty, xm, by], outline=color, width=px(1))
        draw.text((x1 + px(8), (ty + by) / 2), f"cluster {r['cluster_id']}", fill=color, font=font, anchor="lm", **stroke)
        boundaries.setdefault(r["top_y"], []).append(r["top_depth"])
        boundaries.setdefault(r["bottom_y"], []).append(r["bottom_depth"])
    for y, depths in boundaries.items():
        yy = Y(y + y0)
        draw.line([(xd, yy), (x1, yy)], fill=NB.LINE_COLOR, width=px(1))
        label = " / ".join(f"{d:g}" for d in dict.fromkeys(depths))
        draw.text((xd - px(6), yy), label, fill=NB.LINE_COLOR, font=font, anchor="rm", **stroke)

    # colour key on top
    clusters = {}
    for r in results:
        clusters.setdefault(r["lithology"], [])
        if r["cluster_id"] not in clusters[r["lithology"]]:
            clusters[r["lithology"]].append(r["cluster_id"])
    font, row_h = _font(px(15)), px(34)
    texts = {n: n + (f"  (cluster {', '.join(clusters[n])})" if clusters.get(n) else "") for n in colors}
    slots, x, row = [], px(10), 0
    for n in colors:
        w = px(26) + int(draw.textlength(texts[n], font=font)) + px(20)
        if x + w > img.width and x > px(10):
            x, row = px(10), row + 1
        slots.append((n, x, row))
        x += w
    bar = row_h * (row + 1)
    out = Image.new("RGB", (img.width, img.height + bar), (255, 255, 255))
    out.paste(img, (0, bar))
    del img
    draw = ImageDraw.Draw(out)
    for n, x, row in slots:
        y = row * row_h
        draw.rectangle([x, y + px(8), x + px(18), y + px(26)], fill=colors[n], outline=(0, 0, 0), width=px(1))
        draw.text((x + px(26), y + px(17)), texts[n], fill=(0, 0, 0), font=font, anchor="lm")

    save = {"compression": "tiff_lzw"}
    if dpi:
        save["dpi"] = (dpi[0] * scale, dpi[1] * scale)
    out.save(out_path, **save)
    return Path(out_path)
