#!/usr/bin/env python3
"""Cross-check YOLO detection boxes against COCO panoptic Thing segments.

This module only proposes candidates. It never decides which annotation source
is wrong. Human image review is required before adding semantic findings.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from pngutil import read_rgb, segment_id  # noqa: E402


def read_classes(path: Path) -> dict[int, str]:
    result: dict[int, str] = {}
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise ValueError(f"{path}:{line_no}: invalid class row: {line!r}")
        result[int(parts[0])] = parts[1].strip().lower()
    return result


def read_yolo(path: Path, width: int, height: int) -> list[dict[str, Any]]:
    boxes: list[dict[str, Any]] = []
    if not path.is_file():
        return boxes

    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 5:
            raise ValueError(f"{path}:{line_no}: expected 5 fields")

        cls = int(fields[0])
        cx, cy, bw, bh = map(float, fields[1:])
        x1 = (cx - bw / 2) * width
        y1 = (cy - bh / 2) * height
        x2 = (cx + bw / 2) * width
        y2 = (cy + bh / 2) * height

        boxes.append({
            "index": len(boxes),
            "class_id": cls,
            "x1": x1,
            "y1": y1,
            "x2": x2,
            "y2": y2,
        })
    return boxes


def iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    ix1 = max(a["x1"], b["x1"])
    iy1 = max(a["y1"], b["y1"])
    ix2 = min(a["x2"], b["x2"])
    iy2 = min(a["y2"], b["y2"])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)

    area_a = max(0.0, a["x2"] - a["x1"]) * max(0.0, a["y2"] - a["y1"])
    area_b = max(0.0, b["x2"] - b["x1"]) * max(0.0, b["y2"] - b["y1"])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def center_distance(a: dict[str, Any], b: dict[str, Any], width: int, height: int) -> float:
    acx = (a["x1"] + a["x2"]) / 2
    acy = (a["y1"] + a["y2"]) / 2
    bcx = (b["x1"] + b["x2"]) / 2
    bcy = (b["y1"] + b["y2"]) / 2
    diagonal = math.hypot(width, height)
    return math.hypot(acx - bcx, acy - bcy) / diagonal if diagonal else 1.0


def mask_statistics(
    mask_path: Path,
    width: int,
    height: int,
    segments: list[dict[str, Any]],
    boxes: list[dict[str, Any]],
) -> tuple[Counter[int], dict[tuple[int, int], int]]:
    """Return total pixels per segment and pixels of each segment inside each box."""
    mask_width, mask_height, pixels = read_rgb(mask_path)
    if (mask_width, mask_height) != (width, height):
        raise ValueError(
            f"{mask_path}: mask size {mask_width}x{mask_height} "
            f"does not match image size {width}x{height}"
        )

    known_ids = {int(s["id"]) for s in segments}
    totals: Counter[int] = Counter()
    inside: Counter[tuple[int, int]] = Counter()

    # Clamp box coordinates to the image before counting covered mask pixels.
    bounds = []
    for box in boxes:
        x1 = max(0, min(width, math.floor(box["x1"])))
        y1 = max(0, min(height, math.floor(box["y1"])))
        x2 = max(0, min(width, math.ceil(box["x2"])))
        y2 = max(0, min(height, math.ceil(box["y2"])))
        bounds.append((x1, y1, x2, y2))

    for y in range(height):
        row_offset = y * width * 3
        for x in range(width):
            sid = segment_id(pixels, row_offset + x * 3)
            if sid == 0 or sid not in known_ids:
                continue
            totals[sid] += 1

            for box_index, (x1, y1, x2, y2) in enumerate(bounds):
                if x1 <= x < x2 and y1 <= y < y2:
                    inside[(sid, box_index)] += 1

    return totals, dict(inside)


def make_candidates(
    delivery: Path,
    min_segment_area: int = 1024,
    class_iou: float = 0.25,
    drift_coverage: float = 0.80,
    merge_coverage: float = 0.12,
) -> list[dict[str, Any]]:
    root = delivery.resolve()
    panoptic = json.loads((root / "panoptic.json").read_text(encoding="utf-8"))
    classes = read_classes(root / "classes.txt")

    category_by_id = {int(c["id"]): c for c in panoptic["categories"]}
    thing_name_to_category = {
        str(c["name"]).strip().lower(): int(c["id"])
        for c in panoptic["categories"]
        if int(c.get("isthing", 0)) == 1
    }
    yolo_name_to_id = {name: cls_id for cls_id, name in classes.items()}

    image_by_id = {int(im["id"]): im for im in panoptic["images"]}
    annotation_by_image = {
        int(ann["image_id"]): ann for ann in panoptic["annotations"]
    }

    candidates: list[dict[str, Any]] = []

    for image_id, image in image_by_id.items():
        filename = str(image["file_name"])
        stem = Path(filename).stem
        width, height = int(image["width"]), int(image["height"])

        ann = annotation_by_image.get(image_id)
        if ann is None:
            continue

        label_path = root / "labels" / f"{stem}.txt"
        boxes = read_yolo(label_path, width, height)

        # Only Thing segments can correspond to YOLO object detections.
        segments = [
            s for s in ann.get("segments_info", [])
            if int(category_by_id[int(s["category_id"])].get("isthing", 0)) == 1
        ]
        if not segments:
            continue

        mask_path = root / "masks" / str(ann["file_name"])
        if not mask_path.is_file():
            continue

        totals, inside = mask_statistics(mask_path, width, height, segments, boxes)
        segment_by_id = {int(s["id"]): s for s in segments}

        # Convert panoptic [x, y, width, height] boxes to corner coordinates.
        for seg in segments:
            x, y, w, h = map(float, seg["bbox"])
            seg["_box"] = {"x1": x, "y1": y, "x2": x + w, "y2": y + h}
            seg["_name"] = str(category_by_id[int(seg["category_id"])]["name"]).strip().lower()
            seg["_pixels"] = int(totals.get(int(seg["id"]), 0))

        # 1. Find geometrically close box/segment pairs with different class names.
        for box in boxes:
            box_name = classes.get(int(box["class_id"]))
            if box_name is None:
                continue
            alternatives = []
            for seg in segments:
                overlap = iou(box, seg["_box"])
                if overlap >= class_iou and box_name != seg["_name"]:
                    alternatives.append((overlap, seg))

            if alternatives:
                overlap, seg = max(alternatives, key=lambda item: item[0])
                candidates.append({
                    "image": stem,
                    "type": "class_disagreement",
                    "possible_types": ["class_swap", "category_swap"],
                    "details": {
                        "detection_box": box["index"],
                        "detection_class": box_name,
                        "panoptic_segment": int(seg["id"]),
                        "panoptic_class": seg["_name"],
                        "bbox_iou": round(overlap, 4),
                        "instruction": "Inspect the image to decide which source has the wrong class.",
                    },
                })

        # 2. Same-class segment coverage and potential bbox drift.
        for seg in segments:
            sid = int(seg["id"])
            seg_name = seg["_name"]
            seg_pixels = int(seg["_pixels"])
            if seg_pixels < min_segment_area:
                continue

            same_class = [
                box for box in boxes
                if classes.get(int(box["class_id"])) == seg_name
            ]
            same_class_coverages = [
                (inside.get((sid, int(box["index"])), 0) / max(1, seg_pixels), box)
                for box in same_class
            ]

            if same_class_coverages:
                best_coverage, best_box = max(same_class_coverages, key=lambda item: item[0])
                best_iou = iou(best_box, seg["_box"])
                if best_coverage < drift_coverage and best_iou >= 0.05:
                    candidates.append({
                        "image": stem,
                        "type": "bbox_drift",
                        "details": {
                            "segment": sid,
                            "class": seg_name,
                            "segment_pixels": seg_pixels,
                            "best_box": int(best_box["index"]),
                            "mask_coverage_in_box": round(best_coverage, 4),
                            "bbox_iou": round(best_iou, 4),
                        },
                    })

            # 3. A large Thing segment with no plausible same-class box.
            best_same_iou = max(
                (iou(box, seg["_box"]) for box in same_class),
                default=0.0,
            )
            different_class_overlap = max(
                (
                    iou(box, seg["_box"])
                    for box in boxes
                    if classes.get(int(box["class_id"])) != seg_name
                ),
                default=0.0,
            )
            if not same_class and different_class_overlap < class_iou:
                candidates.append({
                    "image": stem,
                    "type": "missing_box",
                    "details": {
                        "segment": sid,
                        "class": seg_name,
                        "segment_pixels": seg_pixels,
                        "reason": "No detection box of this class was found near the segment.",
                    },
                })

            # 4. One panoptic segment covering substantial pixels in two boxes
            # of the same class is a possible merged-instance annotation.
            covered_boxes = []
            for box in same_class:
                coverage = inside.get((sid, int(box["index"])), 0) / max(1, seg_pixels)
                if coverage >= merge_coverage:
                    covered_boxes.append({
                        "box": int(box["index"]),
                        "coverage": round(coverage, 4),
                    })

            if len(covered_boxes) >= 2:
                candidates.append({
                    "image": stem,
                    "type": "instance_merge",
                    "details": {
                        "segment": sid,
                        "class": seg_name,
                        "segment_pixels": seg_pixels,
                        "covered_detection_boxes": covered_boxes,
                    },
                })

    # Stable output and no duplicate candidate records.
    unique: dict[str, dict[str, Any]] = {}
    for item in candidates:
        key = json.dumps(item, sort_keys=True, ensure_ascii=False)
        unique[key] = item
    return sorted(
        unique.values(),
        key=lambda item: (item["image"], item["type"], json.dumps(item["details"], sort_keys=True)),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate human-review candidates from Detection/Panoptic disagreement."
    )
    parser.add_argument("delivery", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--min-segment-area", type=int, default=1024)
    parser.add_argument("--class-iou", type=float, default=0.25)
    parser.add_argument("--drift-coverage", type=float, default=0.80)
    parser.add_argument("--merge-coverage", type=float, default=0.12)
    args = parser.parse_args()

    if args.min_segment_area < 1:
        parser.error("--min-segment-area must be >= 1")
    for name in ("class_iou", "drift_coverage", "merge_coverage"):
        value = getattr(args, name)
        if not 0.0 <= value <= 1.0:
            parser.error(f"--{name.replace('_', '-')} must be between 0 and 1")

    candidates = make_candidates(
        args.delivery,
        min_segment_area=args.min_segment_area,
        class_iou=args.class_iou,
        drift_coverage=args.drift_coverage,
        merge_coverage=args.merge_coverage,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"candidates": candidates}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    counts = Counter(item["type"] for item in candidates)
    print(f"{len(candidates)} candidates -> {args.out}")
    for kind, count in sorted(counts.items()):
        print(f"  {kind}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
