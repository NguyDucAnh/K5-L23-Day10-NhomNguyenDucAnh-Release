

import sys
from pathlib import Path


def check_detection_rules(delivery_dir: Path) -> list[dict]:
    """
    Quét toàn bộ nhãn YOLO trong delivery/labels/*.txt để phát hiện 3 lỗi luật detection.
    Trả về danh sách lỗi: [{"type": <loai_loi>, "target": <image_stem>}, ...]
    """
    errors = []
    labels_dir = Path(delivery_dir) / "labels"
    
    if not labels_dir.is_dir():
        return errors

    # Duyệt qua từng file nhãn .txt (sắp xếp theo thứ tự tên file)
    for label_file in sorted(labels_dir.glob("*.txt")):
        image_id = label_file.stem
        boxes = []

        has_invalid_class = False
        has_out_of_bounds = False
        has_duplicate = False

        with open(label_file, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if not parts or len(parts) < 5:
                    continue

                try:
                    cls_id = int(parts[0])
                    x = float(parts[1])
                    y = float(parts[2])
                    w = float(parts[3])
                    h = float(parts[4])
                except ValueError:
                    has_invalid_class = True
                    continue

                # 1. Kiểm tra invalid_class (Dải hợp lệ của 80 lớp COCO là từ 0 đến 79)
                if cls_id < 0 or cls_id > 79:
                    has_invalid_class = True

                # 2. Kiểm tra bbox_out_of_bounds
                # Tọa độ YOLO: x, y là tâm; w, h là độ rộng và độ cao chuẩn hóa [0, 1]
                # Dung sai float cực nhỏ 1e-5 để tránh sai số làm tròn số thực
                left = x - w / 2.0
                right = x + w / 2.0
                top = y - h / 2.0
                bottom = y + h / 2.0

                if w <= 0 or h <= 0 or left < -1e-5 or top < -1e-5 or right > 1.0 + 1e-5 or bottom > 1.0 + 1e-5:
                    has_out_of_bounds = True

                boxes.append((cls_id, x, y, w, h))

        # Ghi nhận lỗi invalid_class
        if has_invalid_class:
            errors.append({"type": "invalid_class", "target": image_id})

        # Ghi nhận lỗi bbox_out_of_bounds
        if has_out_of_bounds:
            errors.append({"type": "bbox_out_of_bounds", "target": image_id})

        # 3. Kiểm tra duplicate_box: so sánh từng cặp box trong cùng 1 ảnh
        # Đặc điểm lỗi gài: cùng class, cùng kích thước w và h, tâm chỉ lệch 1-2 pixel
        n = len(boxes)
        for i in range(n):
            if has_duplicate:
                break
            c1, x1, y1, w1, h1 = boxes[i]
            for j in range(i + 1, n):
                c2, x2, y2, w2, h2 = boxes[j]

                if c1 == c2:
                    # Kích thước box giống nhau (sai lệch < 1e-4)
                    same_size = (abs(w1 - w2) < 1e-4) and (abs(h1 - h2) < 1e-4)
                    
                    # Tâm lệch không quá 2-3 pixel (chuẩn hóa khoảng <= 0.006)
                    dx = abs(x1 - x2)
                    dy = abs(y1 - y2)
                    near_center = (dx <= 0.006) and (dy <= 0.006)

                    if same_size and near_center:
                        has_duplicate = True
                        break

        if has_duplicate:
            errors.append({"type": "duplicate_box", "target": image_id})

    return errors


if __name__ == "__main__":
    # Hỗ trợ tự chạy test nhanh độc lập từ dòng lệnh
    target_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("runs/r3007/delivery")
    print(f"Đang kiểm tra detection rules trên: {target_dir}")
    findings = check_detection_rules(target_dir)
    print(f"Tìm thấy {len(findings)} lỗi detection:")
    for err in findings:
        print(f" - [{err['type']}] -> target: {err['target']}")
