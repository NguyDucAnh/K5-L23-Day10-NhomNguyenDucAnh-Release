#!/usr/bin/env python3
"""Detect file and metadata consistency errors in a delivery directory."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def safe_delivery_path(root: Path, relative: str) -> Path | None:
    """Resolve a manifest path while refusing paths outside delivery."""
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def validate(delivery: Path) -> list[dict[str, str]]:
    root = delivery.resolve()
    manifest = read_json(root / "manifest.json").get("items", {})
    metadata_rows = read_json(root / "metadata.json").get("items", [])
    jobs = {row.get("job_id", ""): row for row in read_csv(root / "jobs.csv")}
    errors: set[tuple[str, str]] = set()

    # Manifest names are the canonical image targets for file-level checks.
    for image, files in manifest.items():
        rel_image = files.get("image", "")
        image_path = safe_delivery_path(root, rel_image) if rel_image else None
        if image_path is None or not image_path.is_file():
            errors.add(("missing_image", image))
        else:
            digest = hashlib.sha256(image_path.read_bytes()).hexdigest()
            if digest.lower() != str(files.get("image_sha256", "")).strip().lower():
                errors.add(("wrong_hash", image))

    # A scene must have one split across all its metadata rows.
    scene_splits: dict[str, set[str]] = {}
    for row in metadata_rows:
        scene = str(row.get("scene_id", ""))
        split = str(row.get("split", ""))
        if scene and split:
            scene_splits.setdefault(scene, set()).add(split)
    for scene, splits in scene_splits.items():
        if len(splits) > 1:
            errors.add(("scene_leak", scene))

    # Guideline truth comes from the job record, not the per-image metadata.
    for row in metadata_rows:
        image = str(row.get("image", ""))
        job = jobs.get(str(row.get("job_id", "")))
        if image and job and row.get("guideline_version", "") != job.get("guideline", ""):
            errors.add(("guideline_mismatch", image))

    return [{"type": kind, "target": target} for kind, target in sorted(errors)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate delivery file and metadata rules")
    parser.add_argument("delivery", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--candidates", type=Path, help="Reserved for semantic cross-check candidates")
    args = parser.parse_args()
    errors = validate(args.delivery)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"errors": errors}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.candidates:
        args.candidates.parent.mkdir(parents=True, exist_ok=True)
        args.candidates.write_text(json.dumps({"candidates": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(errors)} file/metadata errors -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
