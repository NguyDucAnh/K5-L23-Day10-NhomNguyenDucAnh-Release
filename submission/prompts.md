# Dùng AI

| # | Ai dùng | Công cụ | Prompt (tóm tắt) | AI sai ở đâu | Nhóm kiểm lại thế nào |
|---|---|---|---|---|---|
| 1 | Nguyễn Đức Anh | ChatGPT/Codex | Gợi ý cấu trúc validator cho lỗi file, hash, scene và guideline; cách tích hợp đầu ra JSON. | Gợi ý ban đầu chưa bao phủ đầy đủ detection, panoptic và có thể tạo loại lỗi ngoài codebook. | Nhóm đối chiếu `DELIVERY.md`, ghép đủ 9 lỗi luật, chạy seed 3007 và lô sạch; lô sạch còn 0 lỗi luật. |
| 2 | Hoàng Bá Đạt | ChatGPT/Codex | Giải thích `panoptic.json`, cách đọc PNG RGB bằng `pngutil.py`, bắt `invalid_category_id` và `thing_overlap`. | AI có thể coi mọi sai khác diện tích hoặc vùng chồng là lỗi mà chưa xét `isthing`, `iscrowd` và nền id 0. | Chạy độc lập trên lô sạch và seed 3007, đếm pixel theo segment id, rồi mở ảnh ứng viên kiểm tra bằng mắt. |
| 3 | Đào Duy Hiếu | ChatGPT/Codex | Hướng dẫn trace Tier A/B, lineage, rework queue, manifest, label summary và handover v2. | File CSV tạo lần đầu có BOM làm bộ kiểm không đọc được cột `image` | Nhóm trực tiếp xem và duyệt từng verdict; chạy `check_handover.py`, sửa encoding, đóng gói lại đến khi đạt 17/17 PASS. |
| 4 | Cả nhóm | ChatGPT/Codex | Đối chiếu ảnh COCO công khai với box và panoptic để đề xuất `confirmed/rejected`, phân biệt `class_swap` và `category_swap`. | Một số đề xuất ban đầu chưa chắc chắn ở vật nhỏ, crowd hoặc lớp gần nhau như car/truck, chair/couch, cup/bowl. | Thành viên được phân công mở ảnh, phóng to, kiểm tra label/mask và tự duyệt kết luận; review log ghi rõ tên người xem và bằng chứng cụ thể. |
