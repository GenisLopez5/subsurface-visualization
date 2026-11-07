"""Classical, reference-based document registration without OCR or learned weights.

Semantic labels come exclusively from the training reference bank. Independent
header and footer registrations locate each end of a column; document height is
never used as a substitute for a detected column end. Unknown layouts abstain.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .common import CLASS_NAMES, clamp_box, load_annotation, open_scan, read_crop, write_json
from .inference import InferenceError


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _section_boxes(width: int, height: int) -> dict[str, tuple[int, int, int, int]]:
    length = min(height, width * 5)
    return {"header": (0, 0, width, length), "footer": (0, height - length, width, height)}


def _read_section(scan, box, work_width):
    work_height = max(1, round((box[3] - box[1]) * work_width / scan.width))
    bitmap = read_crop(scan, box, size=(work_width, work_height))
    return cv2.cvtColor(np.asarray(bitmap), cv2.COLOR_RGB2GRAY)


def _features(gray: np.ndarray, max_features: int) -> tuple[np.ndarray, np.ndarray]:
    """Distribute ORB features vertically so a repeated log body cannot dominate."""
    sections = max(1, math.ceil(gray.shape[0] / gray.shape[1]))
    detector = cv2.ORB_create(nfeatures=max(64, math.ceil(max_features / sections)), edgeThreshold=12, fastThreshold=12)
    points, descriptors = [], []
    for index in range(sections):
        start, end = round(index * gray.shape[0] / sections), round((index + 1) * gray.shape[0] / sections)
        # Overlap protects text/keypoints crossing artificial stripe boundaries.
        top, bottom = max(0, start - 16), min(gray.shape[0], end + 16)
        keypoints, desc = detector.detectAndCompute(gray[top:bottom], None)
        if desc is None:
            continue
        for keypoint, descriptor in zip(keypoints, desc):
            y = keypoint.pt[1] + top
            if start <= y < end:
                points.append((keypoint.pt[0], y))
                descriptors.append(descriptor)
    return np.asarray(points, dtype=np.float32).reshape(-1, 2), np.asarray(descriptors, dtype=np.uint8).reshape(-1, 32)


def build_template_bank(
    manifest_path: str | Path,
    output_dir: str | Path,
    split: str = "train",
    work_width: int = 1024,
    max_features: int = 3000,
) -> dict[str, Any]:
    """Build a portable bank from training references only, with leakage checks."""
    if split != "train":
        raise ValueError("Template banks must use split='train'; validation references are forbidden")
    if work_width < 128 or max_features < 128:
        raise ValueError("work_width and max_features must be at least 128")
    manifest_path, output = Path(manifest_path).resolve(), Path(output_dir).resolve()
    if (output / "bank.json").exists():
        raise FileExistsError(f"Template banks are immutable; choose a new output directory: {output}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = manifest.get("records", [])
    if not records:
        raise ValueError("Manifest has no document records")
    train = [record for record in records if record.get("split") == "train"]
    reserved = [record for record in records if record.get("split") != "train"]
    if not train:
        raise ValueError("Manifest has no training documents")
    if any(record.get("split") not in ("train", "val", "test") for record in records):
        raise ValueError("Every document must have a known train/val/test split")
    train_groups = {record["group"] for record in train}
    if train_groups & {record["group"] for record in reserved}:
        raise ValueError("Training and reserved documents share a family; refusing leakage")
    training_images = {record["image_name"] for record in train}
    if training_images & {record["image_name"] for record in reserved}:
        raise ValueError("Training and reserved splits share an image")
    train_paths = {str(Path(record["image_path"]).resolve()) for record in train}
    if train_paths & {str(Path(record["image_path"]).resolve()) for record in reserved}:
        raise ValueError("Training and reserved splits share an image path")
    output.mkdir(parents=True, exist_ok=True)
    references = []
    for index, record in enumerate(train):
        annotation = load_annotation(record["annotation_path"])
        if annotation["image_name"] != record["image_name"]:
            raise ValueError("Manifest image and annotation names disagree")
        arrays = {}
        section_metadata = {}
        with open_scan(record["image_path"]) as scan:
            if scan.size != (annotation["width"], annotation["height"]):
                raise ValueError(f"Image/annotation dimensions disagree: {record['image_name']}")
            for section, box in _section_boxes(scan.width, scan.height).items():
                gray = _read_section(scan, box, work_width)
                points, descriptors = _features(gray, max_features)
                arrays[f"{section}_points"] = points
                arrays[f"{section}_descriptors"] = descriptors
                section_metadata[section] = {"box": list(box), "work_size": [gray.shape[1], gray.shape[0]], "features": len(points)}
        filename = f"reference_{index:03d}.npz"
        np.savez_compressed(output / filename, **arrays)
        references.append({
            "image_name": annotation["image_name"], "group": record["group"], "split": "train",
            "width": annotation["width"], "height": annotation["height"],
            "bounding_boxes": annotation["bounding_boxes"], "sections": section_metadata,
            "features_file": filename, "features_sha256": _sha256(output / filename),
        })
    bank = {
        "version": 1, "method": "orb_header_footer_affine", "work_width": work_width,
        "max_features": max_features, "class_names": list(CLASS_NAMES),
        "source_manifest_sha256": _sha256(manifest_path),
        "training_groups": sorted(train_groups), "training_images": sorted(training_images),
        "references": references,
    }
    write_json(output / "bank.json", bank)
    return bank


def _transform(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    return points @ matrix[:, :2].T + matrix[:, 2]


def _section_points(box, reference, section):
    meta = reference["sections"][section]
    origin = meta["box"]
    sx = meta["work_size"][0] / (origin[2] - origin[0])
    sy = meta["work_size"][1] / (origin[3] - origin[1])
    x1, y1, x2, y2 = box
    return np.asarray([[(x1 - origin[0]) * sx, (y1 - origin[1]) * sy], [(x2 - origin[0]) * sx, (y2 - origin[1]) * sy]], dtype=np.float32)


def _project_box(box, reference, section, matrix, query_meta, query_width, *, legend=False):
    """Project only geometry observed in the registered section.

    Column endpoints outside a header/footer must never affect its X bounds:
    even a fraction of a degree extrapolated across a very tall scan would
    otherwise produce hundreds of pixels of artificial horizontal drift.
    """
    meta = reference["sections"][section]
    origin = meta["box"]
    x1, y1, x2, y2 = box
    if legend:
        native_points = np.asarray([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float64)
    else:
        row = y1 if section == "header" else y2
        native_points = np.asarray([[x1, row], [x2, row]], dtype=np.float64)
    native_points[:, 0] = (native_points[:, 0] - origin[0]) * meta["work_size"][0] / (origin[2] - origin[0])
    native_points[:, 1] = (native_points[:, 1] - origin[1]) * meta["work_size"][1] / (origin[3] - origin[1])
    transformed = _transform(native_points, matrix)
    transformed[:, 0] *= query_width / query_meta["work_size"][0]
    transformed[:, 1] *= (query_meta["box"][3] - query_meta["box"][1]) / query_meta["work_size"][1]
    transformed[:, 1] += query_meta["box"][1]
    left, right = float(transformed[:, 0].min()), float(transformed[:, 0].max())
    if legend:
        return [left, float(transformed[:, 1].min()), right, float(transformed[:, 1].max())]
    row_y = float(transformed[:, 1].mean())
    return [left, row_y, right, row_y]


def _registration(ref_points, ref_desc, query_points, query_desc, *, work_width, section, reference):
    details: dict[str, Any] = {"accepted": False}
    if min(len(ref_desc), len(query_desc)) < 20:
        return None, {**details, "reason": "insufficient_features"}
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(ref_desc, query_desc, k=2)
    matches = [pair[0] for pair in pairs if len(pair) == 2 and pair[0].distance < 0.72 * pair[1].distance]
    # Each target keypoint contributes once, avoiding repeated-symbol votes.
    unique = {}
    for match in sorted(matches, key=lambda match: match.distance):
        unique.setdefault(match.trainIdx, match)
    matches = list(unique.values())
    details["ratio_matches"] = len(matches)
    if len(matches) < 18:
        return None, {**details, "reason": "insufficient_distinct_matches"}
    source = ref_points[[match.queryIdx for match in matches]]
    target = query_points[[match.trainIdx for match in matches]]
    cv2.setRNGSeed(42)
    matrix, mask = cv2.estimateAffinePartial2D(source, target, method=cv2.RANSAC, ransacReprojThreshold=2.5, maxIters=3000, confidence=0.995, refineIters=10)
    if matrix is None or mask is None:
        return None, {**details, "reason": "ransac_failed"}
    inliers = mask.ravel().astype(bool)
    count, fraction = int(inliers.sum()), float(inliers.mean())
    source_good, target_good = source[inliers], target[inliers]
    details.update(inliers=count, inlier_fraction=fraction)
    if count < 18 or fraction < 0.35:
        return None, {**details, "reason": "weak_ransac_consensus"}
    scale = float(np.hypot(matrix[0, 0], matrix[1, 0]))
    angle = math.degrees(math.atan2(matrix[1, 0], matrix[0, 0]))
    residual = float(np.median(np.linalg.norm(_transform(source_good, matrix) - target_good, axis=1)))
    details.update(scale=scale, rotation_degrees=angle, median_error=residual)
    if not (0.75 <= scale <= 1.3 and abs(angle) <= 3 and abs(matrix[0, 2]) <= work_width * 0.2 and abs(matrix[1, 2]) <= work_width * 2 and residual <= 1.8):
        return None, {**details, "reason": "implausible_or_imprecise_transform"}
    span = np.ptp(source_good, axis=0)
    details["coverage"] = [float(value / work_width) for value in span]
    if span[0] < 0.18 * work_width or span[1] < 0.10 * work_width:
        return None, {**details, "reason": "matches_too_local"}
    if section == "header":
        legend = _section_points(reference["bounding_boxes"]["lithology_legend"], reference, section)
        padding = 0.12 * work_width
        near = np.all(source_good >= legend[0] - padding, axis=1) & np.all(source_good <= legend[1] + padding, axis=1)
        anchor_count = int(near.sum())
    else:
        ends = [_section_points(reference["bounding_boxes"][name], reference, section)[1, 1] for name in CLASS_NAMES[:2]]
        anchor_count = int((np.min(np.abs(source_good[:, 1, None] - np.asarray(ends)), axis=1) <= 0.45 * work_width).sum())
    details["semantic_anchor_inliers"] = anchor_count
    if anchor_count < 8:
        return None, {**details, "reason": "no_legend_or_footer_anchor_support"}
    details.update(accepted=True, score=float(count * fraction / (1 + residual)))
    return matrix, details


def _trace_column_end(scan, start, *, max_rows: int = 12000):
    """Follow both projected vertical rules to their last sustained endpoint."""
    x1, y1, x2, _ = start
    x1, x2, top = round(x1), round(x2), max(0, round(y1))
    margin = 5
    left, right = max(0, x1 - margin), min(scan.width, x2 + margin + 1)
    details: dict[str, Any] = {"accepted": False, "start": [x1, top, x2], "crop": [left, top, right, scan.height]}
    if right - left < 3 or scan.height - top < 32:
        return None, {**details, "reason": "insufficient_trace_extent"}
    rows = min(max_rows, scan.height - top)
    with read_crop(scan, (left, top, right, scan.height), size=(right - left, rows)) as bitmap:
        with bitmap.convert("L") as grayscale:
            gray = np.asarray(grayscale, dtype=np.uint8).copy()
    scale_y = (scan.height - top) / rows
    signals = []
    for coordinate in (x1 - left, x2 - left):
        lo, hi = max(0, coordinate - 3), min(gray.shape[1], coordinate + 4)
        signals.append(gray[:, lo:hi].min(axis=1) < 225)
    both = signals[0] & signals[1]
    window = min(61, max(9, rows // 20))
    if window % 2 == 0:
        window += 1
    smooth = np.convolve(both.astype(np.float32), np.ones(window, np.float32) / window, mode="same")
    good = np.flatnonzero(smooth > 0.55)
    details.update(
        rows=rows,
        native_row_scale=scale_y,
        edge_support=[float(signal.mean()) for signal in signals],
        joint_support=float(both.mean()),
    )
    if not len(good):
        return None, {**details, "reason": "projected_edges_not_supported"}
    last = int(good[-1])
    if both[-max(window * 2, 64):].mean() > 0.7:
        endpoint = scan.height
    else:
        endpoint = min(scan.height, round(top + (last + 1) * scale_y))
    prefix = both[: last + 1]
    if prefix.mean() < 0.60:
        return None, {**details, "reason": "column_rules_not_continuous", "proposed_endpoint": endpoint}
    if endpoint - top < max(64, scan.width * 0.15):
        return None, {**details, "reason": "traced_column_too_short", "proposed_endpoint": endpoint}
    details.update(accepted=True, reason="sustained_vertical_rule_endpoint", proposed_endpoint=endpoint)
    return endpoint, details


class ClassicalDetector:
    """Portable training-reference matcher; prediction never opens annotations."""

    def __init__(self, bank_dir: str | Path):
        self.root = Path(bank_dir).resolve()
        self.bank = json.loads((self.root / "bank.json").read_text(encoding="utf-8"))
        if self.bank.get("version") != 1 or tuple(self.bank.get("class_names", ())) != CLASS_NAMES:
            raise ValueError("Unsupported template bank version or classes")
        if any(ref.get("split") != "train" for ref in self.bank.get("references", [])):
            raise ValueError("Template bank contains a non-training reference")
        self.references = []
        for reference in self.bank.get("references", []):
            path = (self.root / reference["features_file"]).resolve()
            if not path.is_relative_to(self.root):
                raise ValueError("Feature files must remain inside the portable bank")
            if _sha256(path) != reference["features_sha256"]:
                raise ValueError(f"Template feature checksum mismatch: {path.name}")
            with np.load(path, allow_pickle=False) as features:
                arrays = {key: features[key].copy() for key in features.files}
            self.references.append((reference, arrays))
        if not self.references:
            raise ValueError("Template bank has no references")

    def predict_document(self, image_path: str | Path, *, diagnostics: dict | None = None) -> dict:
        image_path = Path(image_path)
        width_work = int(self.bank["work_width"])
        query = {}
        with open_scan(image_path) as scan:
            width, height = scan.size
            section_boxes = _section_boxes(width, height)
            for section, box in section_boxes.items():
                gray = _read_section(scan, box, width_work)
                points, descriptors = _features(gray, int(self.bank["max_features"]))
                query[section] = {"points": points, "descriptors": descriptors, "box": box, "work_size": (gray.shape[1], gray.shape[0])}
        details: dict[str, Any] = {"method": "orb_header_footer_affine", "references": [], "selected_reference": None}
        candidates, header_candidates, partial = [], [], {}
        best_partial_score = -1.0
        for reference, features in self.references:
            registrations = {}
            ref_details = {"image_name": reference["image_name"], "group": reference["group"]}
            for section in ("header", "footer"):
                matrix, match = _registration(features[f"{section}_points"], features[f"{section}_descriptors"], query[section]["points"], query[section]["descriptors"], work_width=width_work, section=section, reference=reference)
                registrations[section] = matrix
                ref_details[section] = match
            details["references"].append(ref_details)
            header, footer = registrations["header"], registrations["footer"]
            if header is None:
                continue

            def map_box(name, section):
                return _project_box(reference["bounding_boxes"][name], reference, section, registrations[section], query[section], width, legend=name == "lithology_legend")

            legend = map_box("lithology_legend", "header")
            starts = {name: map_box(name, "header") for name in CLASS_NAMES[:2]}
            if not (0 <= legend[0] < legend[2] <= width and 0 <= legend[1] < legend[3] <= height and legend[3] <= min(box[1] for box in starts.values()) + 0.12 * width):
                ref_details["rejection"] = "legend_not_plausibly_above_columns"
                continue
            if ref_details["header"]["score"] > best_partial_score:
                partial = {"lithology_legend": clamp_box(legend, width, height)}
                best_partial_score = ref_details["header"]["score"]
            header_candidates.append((ref_details["header"]["score"], starts, legend, reference, ref_details))
            if footer is None:
                ref_details["rejection"] = "independent_footer_unresolved"
                continue
            ends = {name: map_box(name, "footer") for name in CLASS_NAMES[:2]}
            boxes = {"lithology_legend": clamp_box(legend, width, height)}
            acceptable = True
            for name in CLASS_NAMES[:2]:
                start, end = starts[name], ends[name]
                disagreement = max(abs(start[0] - end[0]), abs(start[2] - end[2]))
                if disagreement > max(3, width * 0.008):
                    acceptable = False
                    ref_details["rejection"] = "header_footer_x_disagreement"
                    break
                x1, x2 = (start[0] + end[0]) / 2, (start[2] + end[2]) / 2
                y1, y2 = start[1], end[3]
                if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height + 2 and y2 - y1 >= 0.15 * width):
                    acceptable = False
                    ref_details["rejection"] = "column_bounds_implausible"
                    break
                boxes[name] = clamp_box((x1, y1, x2, min(height, y2)), width, height)
            if acceptable:
                score = float(math.sqrt(ref_details["header"]["score"] * ref_details["footer"]["score"]))
                candidates.append((score, boxes, reference))
        if not candidates and header_candidates:
            geometric = []
            header_candidates.sort(key=lambda candidate: candidate[0], reverse=True)
            strongest = header_candidates[0][0]
            with open_scan(image_path) as scan:
                for score, starts, legend, reference, ref_details in header_candidates:
                    if score < strongest * 0.85:
                        break
                    boxes = {"lithology_legend": clamp_box(legend, width, height)}
                    trace_details = {}
                    for name in CLASS_NAMES[:2]:
                        endpoint, trace = _trace_column_end(scan, starts[name])
                        trace_details[name] = trace
                        if endpoint is None:
                            break
                        boxes[name] = clamp_box((starts[name][0], starts[name][1], starts[name][2], endpoint), width, height)
                    ref_details["geometric_footer"] = trace_details
                    if len(boxes) == len(CLASS_NAMES):
                        geometric.append((score, boxes, reference))
            if geometric:
                candidates = geometric
                details["column_end_method"] = "sustained_vertical_rules"
        if candidates:
            candidates.sort(key=lambda candidate: candidate[0], reverse=True)
            score, boxes, reference = candidates[0]
            # Similar-looking headers with conflicting semantic regions are not
            # safely distinguishable through registration alone.
            for alternative_score, alternative_boxes, _ in candidates[1:]:
                if alternative_score < score * 0.85:
                    break
                if any(abs(boxes[name][axis] - alternative_boxes[name][axis]) > width * 0.04 for name in CLASS_NAMES for axis in (0, 2)):
                    details["reason"] = "ambiguous_semantic_template"
                    if diagnostics is not None:
                        diagnostics.update(details)
                    raise InferenceError("Ambiguous matching templates disagree on semantic regions", partial_boxes=partial, diagnostics=details)
            details.update(selected_reference=reference["image_name"], score=score)
            if diagnostics is not None:
                diagnostics.update(details)
            return {"image_name": image_path.name, "width": width, "height": height, "bounding_boxes": {name: boxes[name] for name in CLASS_NAMES}}
        details["reason"] = "no_supported_layout_or_footer"
        if diagnostics is not None:
            diagnostics.update(details)
        raise InferenceError("No reliable training-template header/footer registration; document layout is unsupported", partial_boxes=partial, diagnostics=details)
