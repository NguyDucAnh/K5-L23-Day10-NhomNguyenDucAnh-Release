# Báo cáo nhóm Nguyễn Đức Anh

## 1. Số liệu luyện tập

- Seed nhóm sử dụng: 3007.
- Điểm F1 (rule / cross-check / tổng): 1,0 / 0,6 / 0,8.
- Lô sạch (`--no-faults`) có 3 ứng viên và không có lỗi luật. Lô seed 3007 có 26 ứng viên cần xem lại.

Nhóm dùng ngưỡng IoU 0,35 để ghép box với segment. Trường hợp đã ghép nhưng IoU dưới 0,65 thì được đưa vào danh sách nghi ngờ `bbox_drift`. Với `missing_box`, nhóm chỉ xét các segment đủ lớn để hạn chế báo nhầm các vật quá nhỏ. Các ngưỡng này được chọn sau khi chạy thử trên lô sạch. Sau đó nhóm vẫn mở ảnh và kiểm tra từng ứng viên, không đọc thư mục `_private` và không dựa vào điểm để dò đáp án.

## 2. Vì sao báo hết ứng viên làm F1 giảm?

Không phải ứng viên nào do validator đưa ra cũng là lỗi thật. Với seed 3007, validator tạo ra 26 ứng viên. Sau khi cả nhóm mở ảnh kiểm tra, review log có 13 dòng được xác nhận và 14 dòng bị loại. Số dòng review nhiều hơn số ứng viên một dòng vì nhóm phát hiện thêm một trường hợp `instance_merge` trong lúc xem ảnh.

Nếu nhóm coi tất cả ứng viên là lỗi thì các trường hợp đã bị loại cũng sẽ được ghi vào `errors.json`. Khi đó số false positive tăng, precision giảm và kéo F1 xuống. Vì vậy nhóm chỉ đưa các trường hợp đã được xác nhận vào kết quả cuối.

## 3. Quyết định và lineage

File `errors.json` có 24 lỗi, tương ứng với 21 target khác nhau. Tỷ lệ QC của v1 là 21/250 = 8,4%, cao hơn ngưỡng cho phép 2%, nên nhóm quyết định **HOLD** lô v1.

Nhóm đưa 12 ảnh có lỗi detection vào danh sách rework. Lô v2 còn 238 ảnh và 1.636 nhãn, đồng thời khai báo v1 là lô cha để giữ được lịch sử thay đổi.

Run `R-021` đã được train bằng lô v1. Vì mô hình đã học từ dữ liệu có lỗi nên việc tạo v2 không tự sửa được run cũ. Nhóm cần đánh giá lại `R-021`; nếu kết quả bị ảnh hưởng thì phải train lại bằng dữ liệu đã xử lý.

## 4. Hash chứng minh gì, không chứng minh gì?

SHA256 giúp nhóm kiểm tra file hiện tại có đúng với file đã được ghi trong manifest hay không. Nếu nội dung file bị thay đổi sau khi đóng gói thì mã hash cũng sẽ thay đổi.

Tuy nhiên, hash chỉ kiểm tra tính toàn vẹn của file. Nó không cho biết ảnh có đúng nội dung hay không, box và class có được gán đúng hay không, hoặc dữ liệu có vi phạm yêu cầu bảo mật hay không. Vì vậy với ảnh `000000020247` bị báo `wrong_hash`, nhóm vẫn mở ảnh để kiểm tra. Ảnh đọc được bình thường và đúng nội dung, sau đó nhóm mới ghi hash thật vào manifest của v2.

## 5. Truy vết

Ticket yêu cầu kiểm tra lớp `bottle` tại `scene_197` và `scene_309`.

- Tier A: các ảnh nằm trong hai scene trên, thuộc job sử dụng guideline cũ v1.2 và có box `bottle`. Nhóm tìm được 2 ảnh.
- Tier B: các ảnh khác hai scene trên nhưng vẫn dùng guideline cũ và có lớp `bottle`. Nhóm tìm được 4 ảnh.
- Danh sách `notify` có 5 người từng tham gia gán nhãn hoặc review các job liên quan.

Nhóm lấy phiên bản guideline theo `jobs.csv` thay vì tin trực tiếp trường `guideline_version` của từng ảnh. Lý do là metadata của ảnh cũng có thể đang bị lỗi `guideline_mismatch`.

## 6. Bug hoặc điểm chưa rõ trong lab (nếu có)

Trong quá trình kiểm tra, nhóm chưa ghi nhận vấn đề nào xuất phát từ các script gốc của đề bài. Tuy nhiên, quá trình so sánh kết quả detection và panoptic vẫn xuất hiện nhiều cảnh báo chưa chính xác, đặc biệt ở những trường hợp vật thể có kích thước nhỏ, bị che một phần hoặc các bounding box thuộc những lớp khác nhau bị chồng lấn. Vì vậy, nhóm sử dụng `candidates.json` như một nguồn tham khảo để xác định các trường hợp cần rà soát thêm, thay vì xem toàn bộ các mục trong file là lỗi thực tế.

Ngoài ra, khi tạo file CSV handover bằng PowerShell, file ban đầu có ký tự BOM ở đầu header nên `check_handover.py` không đọc được cột `image`. Nhóm đã lưu lại CSV theo UTF-8 không BOM, chạy kiểm tra lại và đạt 17/17 PASS.
