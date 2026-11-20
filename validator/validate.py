#!/usr/bin/env python3
"""Validate delivery rules and emit conservative det/panoptic review candidates."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from pngutil import read_rgb, segment_id  # noqa: E402


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def safe_delivery_path(root: Path, relative: str) -> Path | None:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def class_names(path: Path) -> dict[int, str]:
    out = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2:
            try:
                out[int(parts[0])] = parts[1]
            except ValueError:
                continue
    return out


def read_boxes(path: Path, width: int, height: int) -> list[dict]:
    boxes = []
    if not path.is_file():
        return boxes
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            cls = int(parts[0])
            x, y, w, h = map(float, parts[1:5])
        except ValueError:
            continue
        boxes.append({"cls": cls, "x": x * width, "y": y * height,
                      "w": w * width, "h": h * height,
                      "x1": (x - w / 2) * width, "y1": (y - h / 2) * height,
                      "x2": (x + w / 2) * width, "y2": (y + h / 2) * height})
    return boxes


def box_iou(a: dict, b: dict) -> float:
    iw = max(0.0, min(a["x2"], b["x2"]) - max(a["x1"], b["x1"]))
    ih = max(0.0, min(a["y2"], b["y2"]) - max(a["y1"], b["y1"]))
    inter = iw * ih
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def validate(delivery: Path) -> tuple[list[dict[str, str]], list[dict]]:
    root = delivery.resolve()
    manifest = read_json(root / "manifest.json").get("items", {})
    metadata_rows = read_json(root / "metadata.json").get("items", [])
    jobs = {row.get("job_id", ""): row for row in read_csv(root / "jobs.csv")}
    errors: set[tuple[str, str]] = set()

    # Check referenced files and hashes from bytes.
    for image, files in manifest.items():
        for key, fault in (("image", "missing_image"), ("label", "missing_label"), ("mask", "missing_mask")):
            rel = files.get(key, "")
            path = safe_delivery_path(root, rel) if rel else None
            if path is None or not path.is_file():
                errors.add((fault, image))
            elif key == "image":
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest.lower() != str(files.get("image_sha256", "")).strip().lower():
                    errors.add(("wrong_hash", image))

    # Scene split and job guideline consistency.
    scene_splits: dict[str, set[str]] = {}
    for row in metadata_rows:
        scene, split = str(row.get("scene_id", "")), str(row.get("split", ""))
        if scene and split:
            scene_splits.setdefault(scene, set()).add(split)
    for scene, splits in scene_splits.items():
        if len(splits) > 1:
            errors.add(("scene_leak", scene))
    for row in metadata_rows:
        image = str(row.get("image", ""))
        job = jobs.get(str(row.get("job_id", "")))
        if image and job and row.get("guideline_version", "") != job.get("guideline", ""):
            errors.add(("guideline_mismatch", image))

    classes = class_names(root / "classes.txt")
    # Detection rules: ids, bounds, and same-size copies within 2 pixels.
    for image, files in manifest.items():
        label_path = safe_delivery_path(root, files.get("label", ""))
        if label_path is None or not label_path.is_file():
            continue
        pan_image = next((im for im in read_json(root / "panoptic.json").get("images", [])
                          if Path(im.get("file_name", "")).stem == image), {})
        width, height = int(pan_image.get("width", 640)), int(pan_image.get("height", 480))
        rows = []
        invalid, out_of_bounds = False, False
        for line in label_path.read_text(encoding="utf-8-sig").splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            try:
                cls = int(parts[0]); x, y, w, h = map(float, parts[1:5])
            except ValueError:
                invalid = True
                continue
            if cls not in classes or not (0 <= cls < 80):
                invalid = True
            if not all(math.isfinite(v) for v in (x, y, w, h)):
                out_of_bounds = True
                continue
            if w <= 0 or h <= 0 or x - w / 2 < -1e-5 or y - h / 2 < -1e-5 or x + w / 2 > 1.00001 or y + h / 2 > 1.00001:
                out_of_bounds = True
            rows.append((cls, x * width, y * height, w * width, h * height))
        if invalid: errors.add(("invalid_class", image))
        if out_of_bounds: errors.add(("bbox_out_of_bounds", image))
        duplicate = any(c1 == c2 and abs(w1-w2) <= 2.0 and abs(h1-h2) <= 2.0
                        and abs(x1-x2) <= 2.5 and abs(y1-y2) <= 2.5
                        for i, (c1,x1,y1,w1,h1) in enumerate(rows)
                        for c2,x2,y2,w2,h2 in rows[i+1:])
        if duplicate: errors.add(("duplicate_box", image))

    # Panoptic category validity and declared area versus actual mask pixels.
    panoptic = read_json(root / "panoptic.json")
    valid_categories = {int(c["id"]) for c in panoptic.get("categories", [])}
    id_to_stem = {im["id"]: Path(im["file_name"]).stem for im in panoptic.get("images", [])}
    segment_rows: dict[str, list[dict]] = {}
    for ann in panoptic.get("annotations", []):
        stem = id_to_stem.get(ann.get("image_id"), Path(ann.get("file_name", "")).stem)
        segments = ann.get("segments_info", [])
        for seg in segments:
            if int(seg.get("category_id", -1)) not in valid_categories:
                errors.add(("invalid_category_id", stem))
        mask_path = safe_delivery_path(root, "masks/" + str(ann.get("file_name", "")))
        counts = Counter()
        if mask_path and mask_path.is_file():
            try:
                _, _, pixels = read_rgb(mask_path)
                for idx in range(0, len(pixels), 3):
                    sid = segment_id(pixels, idx)
                    if sid: counts[sid] += 1
            except (ValueError, OSError):
                pass
        for seg in segments:
            sid = int(seg.get("id", -1))
            isthing = next((c.get("isthing", 0) for c in panoptic.get("categories", [])
                            if int(c.get("id", -1)) == int(seg.get("category_id", -1))), 0)
            if isthing and not seg.get("iscrowd", 0) and sid >= 0 and int(seg.get("area", 0)) > counts.get(sid, 0):
                errors.add(("thing_overlap", stem))
        segment_rows[stem] = segments

    # Cross-source candidates are deliberately conservative and always require image review.
    candidates: set[tuple[str, str]] = set()
    cat_name = {int(c["id"]): c.get("name", "") for c in panoptic.get("categories", [])}
    pan_images = {Path(im.get("file_name", "")).stem: im for im in panoptic.get("images", [])}
    for image, files in manifest.items():
        segs = segment_rows.get(image, [])
        mask_path = safe_delivery_path(root, files.get("mask", ""))
        image_path = safe_delivery_path(root, files.get("image", ""))
        image_meta = pan_images.get(image, {})
        width, height = int(image_meta.get("width", 640)), int(image_meta.get("height", 480))
        if mask_path and mask_path.is_file():
            try: width, height, pixels = read_rgb(mask_path)
            except (ValueError, OSError): pixels = bytearray()
        else: pixels = bytearray()
        label_path = safe_delivery_path(root, files.get("label", ""))
        boxes = read_boxes(label_path, width, height) if label_path else []
        # Use actual mask pixel bounds to avoid trusting panoptic bbox metadata alone.
        sid_bounds: dict[int, list[int]] = {}
        if pixels:
            for idx in range(0, len(pixels), 3):
                sid = segment_id(pixels, idx)
                if not sid: continue
                x, y = (idx // 3) % width, (idx // 3) // width
                b = sid_bounds.setdefault(sid, [x, y, x, y])
                b[0], b[1], b[2], b[3] = min(b[0],x), min(b[1],y), max(b[2],x), max(b[3],y)
        thing_ids = {int(c["id"]) for c in panoptic.get("categories", []) if c.get("isthing", 0)}
        thing_segs = [s for s in segs if not s.get("iscrowd", 0) and int(s.get("category_id", -1)) in thing_ids]
        matched_seg_ids = set()
        box_matches: dict[int, list[int]] = {}
        for bi, box in enumerate(boxes):
            ranked = []
            for si, seg in enumerate(thing_segs):
                bounds = sid_bounds.get(int(seg.get("id", -1)))
                if not bounds: continue
                sb = {"x1":bounds[0],"y1":bounds[1],"x2":bounds[2]+1,"y2":bounds[3]+1,"w":bounds[2]-bounds[0]+1,"h":bounds[3]-bounds[1]+1}
                ranked.append((box_iou(box, sb), si))
            best_iou, best = max(ranked, default=(0.0, -1))
            if best_iou >= 0.35:
                matched_seg_ids.add(best)
                box_matches.setdefault(best, []).append(bi)
                seg_name = cat_name.get(int(thing_segs[best].get("category_id", -1)), "")
                box_name = classes.get(box["cls"], "")
                if seg_name and box_name and seg_name != box_name:
                    candidates.add(("class_swap", image))
        for si, seg in enumerate(thing_segs):
            bounds = sid_bounds.get(int(seg.get("id", -1)))
            if not bounds: continue
            sb = {"x1":bounds[0],"y1":bounds[1],"x2":bounds[2]+1,"y2":bounds[3]+1,"w":bounds[2]-bounds[0]+1,"h":bounds[3]-bounds[1]+1}
            contained = [b for b in boxes if b["x1"] <= sb["x1"]+2 and b["y1"] <= sb["y1"]+2 and b["x2"] >= sb["x2"]-2 and b["y2"] >= sb["y2"]-2]
            if not contained and sb["w"] * sb["h"] >= 500 and si not in matched_seg_ids:
                candidates.add(("missing_box", image))
            if len(box_matches.get(si, [])) >= 2:
                candidates.add(("instance_merge", image))
            if contained:
                # Candidate for drift if mask bounds are noticeably outside the best same-class box.
                siou, bidx = max((box_iou(box, sb), idx) for idx, box in enumerate(boxes))
                if siou < 0.65:
                    candidates.add(("bbox_drift", image))

    return ([{"type": t, "target": target} for t, target in sorted(errors)],
            [{"type": t, "target": target} for t, target in sorted(candidates)])


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a delivery and emit image-review candidates")
    parser.add_argument("delivery", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--candidates", type=Path)
    args = parser.parse_args()
    errors, candidates = validate(args.delivery)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"errors": errors}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.candidates:
        args.candidates.parent.mkdir(parents=True, exist_ok=True)
        args.candidates.write_text(json.dumps({"candidates": candidates}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(errors)} rule errors -> {args.out}")
    if args.candidates:
        print(f"{len(candidates)} review candidates -> {args.candidates}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
