#!/usr/bin/env python3
"""Nguoi 3: Kiem tra loi luat panoptic (invalid_category_id, thing_overlap) va ho tro doc mask PNG."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from pngutil import read_rgb, segment_id  # noqa: E402


def count_mask_pixels(mask_path: Path) -> tuple[int, int, Counter[int]]:
    """Doc file mask PNG 8-bit RGB, tra ve (width, height, so pixel cua tung segment_id).
    Cong thuc COCO panoptic: id = R + G*256 + B*65536. Bo qua id == 0 (nen).
    """
    width, height, pixels = read_rgb(mask_path)
    counts: Counter[int] = Counter()
    for idx in range(0, len(pixels), 3):
        sid = segment_id(pixels, idx)
        if sid != 0:
            counts[sid] += 1
    return width, height, counts


def check_panoptic(delivery: Path) -> list[dict[str, str]]:
    """Bat 2 loi luat panoptic: invalid_category_id va thing_overlap."""
    panoptic = json.loads((delivery / "panoptic.json").read_text(encoding="utf-8"))

    valid_cats = {c["id"] for c in panoptic["categories"]}
    id_to_stem = {im["id"]: Path(im["file_name"]).stem for im in panoptic["images"]}

    found: set[tuple[str, str]] = set()
    for ann in panoptic["annotations"]:
        stem = id_to_stem.get(ann["image_id"], Path(ann["file_name"]).stem)
        segs = ann["segments_info"]

        # Loi 1: category_id khong nam trong danh sach categories
        for seg in segs:
            if seg["category_id"] not in valid_cats:
                found.add(("invalid_category_id", stem))

        # Loi 2: thing_overlap (area khai bao lon hon so pixel thuc te trong mask PNG)
        mask_path = delivery / "masks" / ann["file_name"]
        if mask_path.is_file():
            _, _, counts = count_mask_pixels(mask_path)
            for seg in segs:
                if seg["area"] > counts.get(seg["id"], 0):
                    found.add(("thing_overlap", stem))

    return [{"type": t, "target": tgt} for t, tgt in sorted(found)]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Cach dung: python validator/check_panoptic.py <thu_muc_delivery>")
        sys.exit(1)
    delivery_dir = Path(sys.argv[1])
    errs = check_panoptic(delivery_dir)
    print(f"Panoptic errors ({len(errs)}):")
    for e in errs:
        print(" ", e)
