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
MAX_OUTPUT_PIXELS = 80_000_000  # guard for the notebook-drawn PNG (it builds RGB + RGBA copies at full
                                # output size); the result TIF has no limit, it is drawn in chunks


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


# ---------------------------------------------------------------------------------------------
# Layout: the drawn image is the page (or a strip of it) shrunk by `scale`, with the colour key
# bar added ON TOP. So the drawn image is taller than the page and every y is shifted down:
#
#     y_in_drawn_image = legend_height_px + (y_in_original - y_start) * scale
#
# The layout is saved next to every drawn image as <image>.layout.json (read_layout) and returned
# by the API so a UI can place results (top_y / bottom_y, in original-image px) on the drawing.

def make_layout(page_w, page_h, y0, y1, scale, out_w, out_h) -> dict:
    content_h = round((y1 - y0) * scale)
    return {
        "legend_height_px": out_h - content_h,   # bar on top of the drawn image, in drawn-image px
        "scale": scale,                          # drawn px per original px
        "y_start": y0,                           # first original row drawn (0 unless it is a strip)
        "original_width": page_w, "original_height": page_h,
        "image_width": out_w, "image_height": out_h,
    }


def layout_path(image_path) -> Path:
    image_path = Path(image_path)
    return image_path.with_name(image_path.name + ".layout.json")


def save_layout(image_path, layout: dict) -> dict:
    layout_path(image_path).write_text(json.dumps(layout))
    return layout


def read_layout(image_path) -> dict | None:
    p = layout_path(image_path)
    return json.loads(p.read_text()) if p.exists() else None


def scaled_layout(layout: dict, width: int) -> dict:
    """The layout of the drawn image after resizing it to `width` px wide (as the previews do)."""
    if not layout or layout["image_width"] <= width:
        return layout
    k = width / layout["image_width"]
    return {**layout, "legend_height_px": round(layout["legend_height_px"] * k), "scale": layout["scale"] * k,
            "image_width": width, "image_height": max(1, round(layout["image_height"] * k))}


LAYOUT_HEADERS = ("X-Legend-Height", "X-Scale", "X-Y-Start")

# One sentence for the OpenAPI docs (/docs) of the endpoints that send the layout
LAYOUT_DOC = ("The colour key is added on top of the drawn image, so its rows are shifted down: "
              "`y_drawn = legend_height_px + (y_original - y_start) * scale`.")
LAYOUT_OPENAPI_HEADERS = {
    "X-Legend-Height": {"description": "height of the colour key bar on top of the image, in px of this image "
                                       "(the y shift)", "schema": {"type": "integer"}},
    "X-Scale": {"description": "px of this image per original-image px", "schema": {"type": "number"}},
    "X-Y-Start": {"description": "first original-image row drawn (0 unless the image is a strip)",
                  "schema": {"type": "integer"}},
}


def image_response_doc(media_type: str, note: str = "") -> dict:
    """`responses=` for an image endpoint: its media type and the layout headers it sends."""
    return {200: {"content": {media_type: {}}, "headers": LAYOUT_OPENAPI_HEADERS,
                  "description": "the image; its layout is in the headers" + (f" ({note})" if note else "")}}


def layout_headers(layout: dict | None) -> dict:
    """Response headers for an image endpoint, so the layout comes with the picture itself."""
    if not layout:
        return {}
    return {"X-Legend-Height": str(layout["legend_height_px"]), "X-Scale": f"{layout['scale']:.8g}",
            "X-Y-Start": str(layout["y_start"])}


def default_scale(height: int) -> float:
    """Whole log in one picture of at most ~12k px tall (the notebook's default is 0.25)."""
    return min(0.25, 12000 / height)


def draw(page_path, annotation_path, result, scale, out_dir, page_image=None, y_range=None, tif=False):
    """Write the temp files, run the notebook's visualize_results on them, return the PNG path
    (its layout, with the legend height, is saved next to it: read_layout(path)) (and also the full-resolution .tif next to it when tif=True, see render_tif).

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
    if y_range is None:
        y0, y1 = 0, page.height
    if out_w * out_h > MAX_OUTPUT_PIXELS:
        raise ValueError(f"output image would be {int(out_w)}x{int(out_h)} px; use a smaller scale")

    out = tmp / "result.png"
    with contextlib.redirect_stdout(io.StringIO()):  # the notebook prints a line per call
        view = NB.visualize_results(str(img_file), str(ann_file), str(tmp / "results.csv"), scale=scale,
                                    save_path=str(out), colors=colors)
    save_layout(out, make_layout(page.width, page.height, y0, y1, scale, view.width, view.height))
    if tif:
        render_tif(page, annotation_path, result, tmp / "result.tif", y_range=y_range, colors=colors)
    return out


# ---------------------------------------------------------------------------------------------
# Result TIF: same layout and colours as the notebook's visualize_results, but always drawn at full
# page resolution (text, lines and the legend scaled up to match), so it is as sharp as the source
# scan. The notebook code itself (notebook_viz.py) is left unchanged.
#
# Low memory: the full-size RGB picture is never held in memory. It is drawn CHUNK_ROWS rows at a
# time (each chunk gets everything that overlaps it; Pillow clips the rest) and every chunk is
# compressed and written to the TIF straight away (StripTiffWriter). The pixels are the same as
# drawing the whole picture at once. Peak memory ~ one chunk (width x CHUNK_ROWS x 3 bytes) plus
# the 1-bit page, instead of 2-3x the full RGB picture.

NB_SCALE = 0.25      # the notebook's sizes (fonts, lines, legend) are meant for this scale
CHUNK_ROWS = 2048    # rows drawn at a time
STRIP_ROWS = 256     # rows per TIF strip (several strips per chunk)


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


class StripTiffWriter:
    """Writes an RGB TIF (Deflate-compressed, lossless) strip by strip: rows go in with add(), each
    strip is compressed and written as soon as it is complete, so the image is never in memory whole.
    Plain baseline TIF (strips, like Pillow writes), readable by Pillow, libtiff, GDAL, etc."""

    def __init__(self, path, width, height, dpi=None, rows_per_strip=STRIP_ROWS, level=6):
        import struct
        import zlib
        self._struct, self._zlib = struct, zlib
        self.f = open(path, "wb")
        self.w, self.h, self.dpi, self.rps, self.level = width, height, dpi, rows_per_strip, level
        self.offsets, self.counts, self.pending, self.rows_in = [], [], b"", 0
        self.f.write(b"II*\x00" + struct.pack("<I", 0))  # little-endian header; IFD offset patched in close()

    def add(self, rgb_image):
        """Append the next rows (a PIL RGB image `width` px wide)."""
        assert rgb_image.mode == "RGB" and rgb_image.width == self.w
        self.pending += rgb_image.tobytes()
        self.rows_in += rgb_image.height
        strip = self.w * 3 * self.rps
        while len(self.pending) >= strip:
            self._strip(self.pending[:strip])
            self.pending = self.pending[strip:]

    def _strip(self, raw: bytes):
        data = self._zlib.compress(raw, self.level)
        self.offsets.append(self.f.tell())
        self.counts.append(len(data))
        self.f.write(data)
        if self.f.tell() >= 2**32:
            raise ValueError("TIF over 4 GB")

    def close(self):
        if self.pending:
            self._strip(self.pending)
            self.pending = b""
        if self.rows_in != self.h:
            raise ValueError(f"TIF got {self.rows_in} rows, expected {self.h}")
        pk, f = self._struct.pack, self.f
        if f.tell() % 2:
            f.write(b"\x00")
        extra = []  # (bytes) values that don't fit in a tag entry, written after the IFD

        def tag(code, typ, values):
            size = {3: 2, 4: 4, 5: 8}[typ]
            fmt = {3: "H", 4: "I"}.get(typ)
            raw = (b"".join(pk("<II", *v) for v in values) if typ == 5 else pk(f"<{len(values)}{fmt}", *values))
            return code, typ, len(values), raw

        xres = (round((self.dpi[0] if self.dpi else 72) * 100), 100)
        yres = (round((self.dpi[1] if self.dpi else 72) * 100), 100)
        tags = [tag(256, 4, [self.w]), tag(257, 4, [self.h]), tag(258, 3, [8, 8, 8]), tag(259, 3, [8]),
                tag(262, 3, [2]), tag(273, 4, self.offsets), tag(277, 3, [3]), tag(278, 4, [self.rps]),
                tag(279, 4, self.counts), tag(282, 5, [xres]), tag(283, 5, [yres]), tag(284, 3, [1]),
                tag(296, 3, [2])]
        ifd = f.tell()
        data_at = ifd + 2 + 12 * len(tags) + 4
        entries = b""
        for code, typ, n, raw in tags:
            if len(raw) <= 4:
                entries += pk("<HHI", code, typ, n) + raw.ljust(4, b"\x00")
            else:
                entries += pk("<HHII", code, typ, n, data_at)
                extra.append(raw)
                data_at += len(raw) + len(raw) % 2
        f.write(pk("<H", len(tags)) + entries + pk("<I", 0))
        for raw in extra:
            f.write(raw + b"\x00" * (len(raw) % 2))
        f.seek(4)
        f.write(pk("<I", ifd))
        f.close()


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


def render_tif(page, annotation_path, result, out_path, y_range=None, colors=None, key_path=None):
    """Draw the results on the page (or the strip y_range of it) at full resolution and save a
    Deflate-compressed TIF that keeps the source DPI, chunk by chunk (low memory, see above).
    key_path: also save the colour key bar on its own (the exact bar on top of the TIF, same width,
    height = legend_height_px) as a PNG there.
    Returns the layout (see make_layout), which is also saved next to the TIF."""
    from PIL import ImageDraw

    page = page if isinstance(page, Image.Image) else Image.open(page)
    dpi = page.info.get("dpi")
    boxes = json.loads(Path(annotation_path).read_text())["bounding_boxes"]
    y0, y1 = 0, page.height
    if y_range is not None:
        y0, y1 = max(0, int(y_range[0])), min(page.height, int(y_range[1]))
    scale = 1.0
    results = _intervals_for(result, y0, y1 - y0) if y_range is not None else _intervals_for(result)
    colors = colors or NB.build_color_map(r["lithology"] for r in results)
    k = max(1.0, scale / NB_SCALE)           # size factor for everything the notebook draws in fixed pixels
    px = lambda v: max(1, round(v * k))      # noqa: E731
    W, H = page.width, y1 - y0

    # colour key layout (needs only the width), so the bar height is known before drawing
    clusters = {}
    for r in results:
        clusters.setdefault(r["lithology"], [])
        if r["cluster_id"] not in clusters[r["lithology"]]:
            clusters[r["lithology"]].append(r["cluster_id"])
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    key_font, row_h = _font(px(15)), px(34)
    texts = {n: n + (f"  (cluster {', '.join(clusters[n])})" if clusters.get(n) else "") for n in colors}
    slots, x, row = [], px(10), 0
    for n in colors:
        w = px(26) + int(measure.textlength(texts[n], font=key_font)) + px(20)
        if x + w > W and x > px(10):
            x, row = px(10), row + 1
        slots.append((n, x, row))
        x += w
    bar = row_h * (row + 1)
    out_h = bar + H

    def draw_key(draw, dy):
        """The colour key bar, drawn with its top at row dy of `draw`'s image."""
        draw.rectangle([0, dy, W, bar - 1 + dy], fill=(255, 255, 255))
        for n, x, row in slots:
            y = row * row_h + dy
            draw.rectangle([x, y + px(8), x + px(18), y + px(26)], fill=colors[n], outline=(0, 0, 0), width=px(1))
            draw.text((x + px(26), y + px(17)), texts[n], fill=(0, 0, 0), font=key_font, anchor="lm")

    if key_path is not None:
        key = Image.new("RGB", (W, bar), (255, 255, 255))
        draw_key(ImageDraw.Draw(key), 0)
        key.save(key_path, dpi=dpi) if dpi else key.save(key_path)
        del key

    # everything in output-image coordinates (bar on top, then the page from y0 down)
    S = lambda v: v * scale                  # noqa: E731  page px -> output px
    Y = lambda v: bar + (v - y0) * scale     # noqa: E731
    col, dep = boxes["interpreted_lithology_column"], boxes["depth_column"]
    x0, x1 = S(col[0]), S(col[2])
    xm, xd = x0 + (x1 - x0) * 0.5, S(dep[0])
    box_font, font = _font(px(12)), _font(px(13))
    stroke = dict(stroke_width=px(3), stroke_fill=(255, 255, 255))
    iv = [(r, Y(r["top_y"] + y0), Y(r["bottom_y"] + y0)) for r in results]
    boundaries = {}
    for r in results:
        boundaries.setdefault(r["top_y"], []).append(r["top_depth"])
        boundaries.setdefault(r["bottom_y"], []).append(r["bottom_depth"])
    margin = px(60)  # text / strokes reach this far past their anchor point

    writer = StripTiffWriter(out_path, W, out_h, dpi=(dpi[0] * scale, dpi[1] * scale) if dpi else None)
    try:
        for cs in range(0, out_h, CHUNK_ROWS):
            ce = min(cs + CHUNK_ROWS, out_h)
            img = Image.new("RGB", (W, ce - cs), (255, 255, 255))
            ps, pe = max(cs, bar), ce                     # page rows of this chunk, in output px
            if pe > ps:
                img.paste(NB.crop_rgb(page, (0, y0 + ps - bar, W, y0 + pe - bar)), (0, ps - cs))
            draw = ImageDraw.Draw(img)
            near = lambda a, b: b >= cs - margin and a <= ce + margin  # noqa: E731

            # annotation boxes
            for i, (name, (bx0, by0, bx1, by1)) in enumerate(boxes.items()):
                rect = [S(bx0), Y(by0), S(bx1) - 1, Y(by1) - 1]
                if rect[3] < bar or rect[1] > out_h:
                    continue
                color = NB.BOX_COLORS.get(name, (0, 200, 0))
                draw.rectangle([rect[0], rect[1] - cs, rect[2], rect[3] - cs], outline=color, width=px(2))
                draw.text((rect[0] + px(6), max(rect[1], bar) + px(4) + px(16) * i - cs), name, fill=color,
                          font=box_font)

            # intervals: translucent fill over the left half of the column
            for r, ty, by in iv:
                top, bottom = round(ty), max(round(ty) + 1, round(by))
                a, b = max(top, cs), min(bottom, ce)
                if b > a:
                    box = (round(x0), a - cs, round(xm), b - cs)
                    region = img.crop(box)
                    img.paste(Image.blend(region, Image.new("RGB", region.size, colors[r["lithology"]]), 150 / 255),
                              box[:2])
            draw = ImageDraw.Draw(img)
            # outlines, cluster ids, boundaries + depths
            for r, ty, by in iv:
                if near(ty, by):
                    color = colors[r["lithology"]]
                    draw.rectangle([x0, ty - cs, xm, by - cs], outline=color, width=px(1))
                    draw.text((x1 + px(8), (ty + by) / 2 - cs), f"cluster {r['cluster_id']}", fill=color, font=font,
                              anchor="lm", **stroke)
            for y, depths in boundaries.items():
                yy = Y(y + y0)
                if near(yy, yy):
                    draw.line([(xd, yy - cs), (x1, yy - cs)], fill=NB.LINE_COLOR, width=px(1))
                    label = " / ".join(f"{d:g}" for d in dict.fromkeys(depths))
                    draw.text((xd - px(6), yy - cs), label, fill=NB.LINE_COLOR, font=font, anchor="rm", **stroke)

            # colour key (top bar)
            if cs < bar:
                draw_key(draw, -cs)
            writer.add(img)
            del img, draw
    except BaseException:
        writer.f.close()
        Path(out_path).unlink(missing_ok=True)
        raise
    writer.close()
    return save_layout(out_path, make_layout(page.width, page.height, y0, y1, scale, W, out_h))


def crop_legend(page, annotation_path, out_path):
    """Cut the log's own legend (the `lithology_legend` box) out of the original page, at full
    resolution, and save it as a PNG. Returns the box used (original-image px), or None if the
    annotation has no usable legend box."""
    page = page if isinstance(page, Image.Image) else Image.open(page)
    box = json.loads(Path(annotation_path).read_text())["bounding_boxes"].get("lithology_legend")
    if not box:
        return None
    x0, y0 = max(0, int(box[0])), max(0, int(box[1]))
    x1, y1 = min(page.width, int(round(box[2]))), min(page.height, int(round(box[3])))
    if x1 <= x0 or y1 <= y0:
        return None
    crop = page.crop((x0, y0, x1, y1))
    if crop.mode not in ("RGB", "L", "1"):
        crop = crop.convert("RGB")
    dpi = page.info.get("dpi")
    crop.save(out_path, dpi=dpi) if dpi else crop.save(out_path)
    return [x0, y0, x1, y1]
