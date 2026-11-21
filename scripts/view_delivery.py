#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def find_file(root, name):
    path = root / name
    if path.exists():
        return path
    matches = list(root.rglob(name))
    if matches:
        return matches[0]
    raise FileNotFoundError(f"Không tìm thấy: {name}")


def load_classes(path):
    result = {}
    for line in path.read_text().splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2:
            result[int(parts[0])] = parts[1]
    return result


def load_font():
    for name in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ):
        if Path(name).exists():
            return ImageFont.truetype(name, 15)
    return ImageFont.load_default()


def draw_label(draw, xy, text, color, font):
    x, y = xy
    bounds = draw.textbbox((x, y), text, font=font)
    draw.rectangle(bounds, fill=(0, 0, 0))
    draw.text((x, y), text, fill=color, font=font)


def add_title(image, title):
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, image.width, 27), fill=(0, 0, 0))
    draw.text((8, 5), title, fill="white", font=load_font())
    return image


def load_candidates(path, stem):
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        data = data.get("candidates", [])

    boxes, segments = set(), set()
    box_types, segment_types = {}, {}

    def add_box(index, error_type):
        if index is not None:
            index = int(index)
            boxes.add(index)
            box_types.setdefault(index, set()).add(error_type)

    def add_segment(sid, error_type):
        if sid is not None:
            sid = int(sid)
            segments.add(sid)
            segment_types.setdefault(sid, set()).add(error_type)

    for item in data:
        if Path(str(item.get("image", ""))).stem != stem:
            continue

        error_type = item.get("type", "unknown")
        details = item.get("details", {})

        if error_type == "class_disagreement":
            add_box(details.get("detection_box"), error_type)
            add_segment(details.get("panoptic_segment"), error_type)

        elif error_type == "bbox_drift":
            add_box(details.get("best_box"), error_type)
            add_segment(details.get("segment"), error_type)

        elif error_type == "instance_merge":
            add_segment(details.get("segment"), error_type)
            for item_box in details.get("covered_detection_boxes", []):
                add_box(item_box.get("box"), error_type)

        elif error_type == "missing_box":
            add_segment(details.get("segment"), error_type)

    return boxes, segments, box_types, segment_types


def get_panoptic_data(root, stem):
    data = json.loads((root / "panoptic.json").read_text())

    image_info = next(
        (x for x in data["images"]
         if Path(x["file_name"]).stem == stem),
        None,
    )
    if image_info is None:
        raise ValueError(f"Không tìm thấy ảnh {stem} trong panoptic.json")

    annotation = next(
        (x for x in data["annotations"]
         if x["image_id"] == image_info["id"]),
        None,
    )
    if annotation is None:
        raise ValueError(f"Không tìm thấy annotation của {stem}")

    mask_path = find_file(root, annotation["file_name"])
    categories = {int(x["id"]): x for x in data["categories"]}
    return mask_path, annotation, categories


def draw_detection(image, label_path, classes, selected=None,
                   types_by_box=None, all_mode=False):
    draw = ImageDraw.Draw(image)
    font = load_font()
    width, height = image.size

    for index, line in enumerate(label_path.read_text().splitlines()):
        parts = line.split()
        if len(parts) < 5:
            continue
        if selected is not None and index not in selected:
            continue

        class_id = int(parts[0])
        cx, cy, bw, bh = map(float, parts[1:5])

        x1 = (cx - bw / 2) * width
        y1 = (cy - bh / 2) * height
        x2 = (cx + bw / 2) * width
        y2 = (cy + bh / 2) * height

        color = (0, 255, 80)
        draw.rectangle((x1, y1, x2, y2), outline=color, width=3)

        name = classes.get(class_id, f"class_{class_id}")
        if all_mode:
            text = f"D:{name} | box {index}"
        else:
            kinds = ",".join(sorted((types_by_box or {}).get(index, [])))
            text = f"D:{name} | box {index} | {kinds}"

        draw_label(draw, (x1, max(28, y1 - 20)), text, color, font)


def draw_panoptic(image, mask_path, annotation, categories,
                  selected=None, types_by_segment=None):
    mask = Image.open(mask_path).convert("RGB")
    pixels = mask.load()
    mask_width, mask_height = mask.size

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_pixels = overlay.load()
    draw = ImageDraw.Draw(image)
    font = load_font()

    for segment in annotation["segments_info"]:
        sid = int(segment["id"])

        # selected=None nghĩa là vẽ TẤT CẢ segment, cả Thing lẫn Stuff.
        if selected is not None and sid not in selected:
            continue

        category = categories[int(segment["category_id"])]
        name = category["name"]

        x, y, w, h = map(int, segment["bbox"])
        x0, y0 = max(0, x), max(0, y)
        x1 = min(mask_width, x + w)
        y1 = min(mask_height, y + h)

        for py in range(y0, y1):
            for px in range(x0, x1):
                r, g, b = pixels[px, py]
                pixel_id = r * 65536 + g * 256 + b
                if pixel_id != sid:
                    continue

                # Chỉ tô pixel nằm trên biên của segment.
                neighbors = []
                if px > 0:
                    neighbors.append(pixels[px - 1, py])
                if px + 1 < mask_width:
                    neighbors.append(pixels[px + 1, py])
                if py > 0:
                    neighbors.append(pixels[px, py - 1])
                if py + 1 < mask_height:
                    neighbors.append(pixels[px, py + 1])

                if any(
                    nr * 65536 + ng * 256 + nb != sid
                    for nr, ng, nb in neighbors
                ):
                    if px < image.width and py < image.height:
                        overlay_pixels[px, py] = (255, 50, 220, 255)

        color = (255, 50, 220)
        draw.rectangle((x, y, x + w, y + h), outline=color, width=3)

        if selected is None:
            text = f"P:{name} | seg {sid}"
        else:
            kinds = ",".join(
                sorted((types_by_segment or {}).get(sid, []))
            )
            text = f"P:{name} | seg {sid} | {kinds}"

        draw_label(draw, (x, max(28, y - 20)), text, color, font)

    merged = Image.alpha_composite(image.convert("RGBA"), overlay)
    image.paste(merged.convert("RGB"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("delivery")
    parser.add_argument("--image", required=True)
    args = parser.parse_args()

    root = Path(args.delivery)
    stem = Path(args.image).stem

    image_path = find_file(root / "images", stem + ".jpg")
    label_path = find_file(root / "labels", stem + ".txt")
    classes = load_classes(root / "classes.txt")
    mask_path, annotation, categories = get_panoptic_data(root, stem)

    original = Image.open(image_path).convert("RGB")

    # 1. Ảnh gốc.
    original_view = original.copy()

    # 2. Tất cả Detection.
    detection = original.copy()
    draw_detection(
        detection, label_path, classes,
        selected=None, all_mode=True,
    )
    add_title(detection, "2 - ALL DETECTION")

    # 3. Tất cả Panoptic.
    panoptic = original.copy()
    draw_panoptic(
        panoptic, mask_path, annotation, categories,
        selected=None,
    )
    add_title(panoptic, "3 - ALL PANOPTIC")

    # 4. Tổng hợp tất cả hai nguồn.
    combined = original.copy()
    draw_detection(
        combined, label_path, classes,
        selected=None, all_mode=True,
    )
    draw_panoptic(
        combined, mask_path, annotation, categories,
        selected=None,
    )
    add_title(combined, "4 - ALL DETECTION + ALL PANOPTIC")

    total_detection = sum(
        1 for line in label_path.read_text().splitlines()
        if line.strip()
    )
    total_panoptic = len(annotation["segments_info"])

    print(f"Image: {stem}")
    print(f"Detection boxes: {total_detection}")
    print(f"Panoptic segments: {total_panoptic}")
    print("Candidates: DISABLED")
    print("Filtering: DISABLED")
    print("Opening 4 views...")

    original_view.show()
    detection.show()
    panoptic.show()
    combined.show()


if __name__ == "__main__":
    main()
