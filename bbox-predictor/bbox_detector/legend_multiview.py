"""Retry uncertain legends with overlapping views and independent edge votes."""
from __future__ import annotations

import math
from collections import defaultdict

from .common import clamp_box, open_scan, read_crop
from .legend_geometry import legend_band


def merge_edge_votes(seed, candidates, *, width, min_views=2):
    """Expand a connected seed only towards edges supported by distinct views.

    An edge clipped by an internal crop boundary cannot vote. Multiple boxes
    from one crop count as one view, preventing a noisy crop from dominating.
    No region identity or annotation information is used here.
    """
    if min_views < 2:
        raise ValueError("At least two independent views must support an edge")
    sh = seed[3]-seed[1]
    eligible = []
    for candidate in candidates:
        box = candidate["box"]
        intersection = max(0, min(seed[3], box[3])-max(seed[1], box[1]))
        extent = max(seed[3], box[3])-min(seed[1], box[1])
        if extent > 0 and intersection/extent >= .7:
            eligible.append(candidate)
    connected, remaining = [], eligible
    span = list(seed)
    while remaining:
        deferred = []
        for candidate in remaining:
            box = candidate["box"]
            if max(0, span[0]-box[2], box[0]-span[2]) <= .05*width:
                connected.append(candidate)
                span[0], span[2] = min(span[0], box[0]), max(span[2], box[2])
            else:
                deferred.append(candidate)
        if len(deferred) == len(remaining):
            break
        remaining = deferred
    result, votes = list(seed), {}
    for edge, name in enumerate(("xmin", "ymin", "xmax", "ymax")):
        tolerance = max(2, (.015*width if edge in (0, 2) else .03*sh))
        available = [c for c in connected if c["edge_visible"][edge]]
        choices = []
        for anchor in available:
            by_view = defaultdict(list)
            for candidate in available:
                if abs(candidate["box"][edge]-anchor["box"][edge]) <= tolerance:
                    by_view[candidate["view_id"]].append(candidate)
            if len(by_view) < min_views:
                continue
            members = [max(group, key=lambda c: c["confidence"]) for group in by_view.values()]
            total = sum(c["confidence"] for c in members)
            position = sum(c["box"][edge]*c["confidence"] for c in members)/total
            choices.append((position, len(by_view)))
        if choices:
            position, support = (min(choices) if edge < 2 else max(choices))
            result[edge] = min(result[edge], position) if edge < 2 else max(result[edge], position)
            votes[name] = {"position": position, "support_views": support}
    # Guard against a disconnected neighbour captured by a loose detector.
    if result[2]-result[0] > 1.5*(seed[2]-seed[0]) or result[3]-result[1] > 1.3*sh:
        return list(seed), {"reason": "excessive_expansion", "edge_votes": votes}
    return result, {"reason": "independent_edge_votes" if votes else "insufficient_independent_votes",
                    "edge_votes": votes, "connected_candidates": len(connected)}


def refine_legend_multiview(image_path, seed, *, column_top, detector, imgsz=2048,
                            confidence=.05, device=None, diagnostics=None):
    """Two complete-width views and three overlapping horizontal subcrops.

    Decode one bounded band, reuse its pixels for every view, map each detection
    to original coordinates, and preserve the seed when evidence is insufficient.
    """
    if not isinstance(imgsz, int) or isinstance(imgsz, bool) or imgsz < 32 or imgsz % 32:
        raise ValueError("Multiview imgsz must be a positive multiple of 32")
    if not math.isfinite(confidence) or not 0 < confidence <= 1:
        raise ValueError("Multiview confidence must be in (0, 1]")
    candidates, views = [], []
    with open_scan(image_path) as scan:
        band = legend_band(scan.width, scan.height, seed, column_top)
        target_width = min(scan.width, 2048)
        target_height = max(1, round((band[3]-band[1])*target_width/scan.width))
        with read_crop(scan, band, size=(target_width, target_height)) as patch:
            sx, sy = scan.width/patch.width, (band[3]-band[1])/patch.height
            local_top, local_bottom = (seed[1]-band[1])/sy, (seed[3]-band[1])/sy
            extent = local_bottom-local_top
            boxes = [
                ("context", (0,0,patch.width,patch.height), imgsz),
                ("tight", (0,max(0,round(local_top-.25*extent)),patch.width,
                           min(patch.height,round(local_bottom+.25*extent))), imgsz),
            ]
            for label, fraction in (("left",0), ("center",1/6), ("right",1/3)):
                left = round(patch.width*fraction)
                boxes.append((label, (left,0,min(patch.width,left+round(patch.width*2/3)),patch.height),
                              max(32, round(imgsz*.75/32)*32)))
            for view_id, (label, box, resolution) in enumerate(boxes):
                native_crop = [box[0]*sx, band[1]+box[1]*sy, box[2]*sx, band[1]+box[3]*sy]
                views.append({"view_id": view_id, "name": label, "box": native_crop, "imgsz": resolution})
                with patch.crop(box) as crop:
                    for raw in detector.predict(crop, imgsz=resolution, confidence=confidence, device=device):
                        if raw.class_id != 0 or not math.isfinite(raw.confidence) or raw.confidence < confidence:
                            continue
                        if len(raw.box) != 4 or not all(math.isfinite(v) for v in raw.box):
                            raise ValueError("Malformed multiview detection")
                        x1,y1,x2,y2 = raw.box
                        x1,x2 = max(0,x1),min(crop.width,x2)
                        y1,y2 = max(0,y1),min(crop.height,y2)
                        if x2 <= x1 or y2 <= y1 or y1 <= 1 or y2 >= crop.height-1:
                            continue
                        native = [(box[0]+x1)*sx, band[1]+(box[1]+y1)*sy,
                                  (box[0]+x2)*sx, band[1]+(box[1]+y2)*sy]
                        if (native[1]+native[3])/2 >= column_top:
                            continue
                        candidates.append({"view_id": view_id, "box": native, "confidence": raw.confidence,
                                           "edge_visible": [x1>1 or box[0]==0, True,
                                                            x2<crop.width-1 or box[2]==patch.width, True]})
        # Retain weak side pieces relative to the strongest eligible evidence.
        best = max((c["confidence"] for c in candidates), default=confidence)
        candidates = [c for c in candidates if c["confidence"] >= max(confidence, .3*best)]
        merged, details = merge_edge_votes(seed, candidates, width=scan.width)
        result = clamp_box([round(v,9) for v in merged], scan.width, scan.height)
    if diagnostics is not None:
        diagnostics.update(details, views=views, candidates=candidates, selected_box=result)
    return result
