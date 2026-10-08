# Reconciliation dashboards

Ba dashboard được provision từ `docker/grafana/dashboards`; Grafana kiểm tra thay đổi file mỗi 30 giây.

## 1. Runs & Operations

Mở `/d/paymenthub-reconciliation` trên Grafana.

- Bảng run hiển thị PENDING, RUNNING, COMPLETED và FAILED. Lọc theo cột Status, Region, Tenant, Provider hoặc Business date.
- Bấm Run ID để mở dashboard chi tiết. Link cũng có trên bảng DQ và run gần nhất của freshness.
- Alert lấy từ Prometheus, giới hạn ba rule reconciliation hiện có.
- Freshness đọc `/_meta/freshness`: trạng thái, lý do, lịch dự kiến, SLA, số lần lỗi liên tiếp và last success. API cần cấu hình `RECON_SCHEDULES_JSON`; bảng rỗng không có nghĩa mọi scope đều healthy.
- Daily Data Quality đọc `/internal/data-quality?limit=500`: năm rule cho mỗi run, PASSED/FAILED, severity, số dòng bị ảnh hưởng và result keys.

Các bảng API không chịu bộ lọc thời gian của Grafana. Run list giới hạn 50 trang × 200 run, sắp xếp Started trong dữ liệu đã tải; API hiện phân trang theo UUID, vì vậy khi vượt giới hạn không bảo đảm đây là 10.000 run mới nhất. DQ chỉ hiển thị 500 finding gần nhất theo business date, không đại diện toàn bộ lịch sử. Có thể lọc các cột trong bảng để điều tra tập dữ liệu đã tải.

Freshness đánh giá theo thời gian thực của API. Dữ liệu mô phỏng ngày cũ có thể cho MISSING ở ngày hiện tại. `Seconds past SLA` là tuổi của deadline do API trả về, cần đọc cùng trạng thái/reason, không tự xem là độ trễ hoàn tất của một run đã COMPLETED.

## 2. Run Detail

Mở từ Run ID trên dashboard 1, hoặc `/d/paymenthub-reconciliation-api?var-run_id=<UUID>`.

Giữ các mục 2–4 của dashboard cũ: run overview, input volume, outcome distribution, currency balance, refund analysis, breakdown, transaction evidence và download CSV/XLSX/Parquet. Có link quay về Runs & Operations.

Run ID là textbox đồng bộ URL, nên link trực tiếp vẫn mở được run không nằm trong trang danh sách hiện tại. Có thể dán UUID khác vào ô này. Với run chưa hoàn tất, summary chỉ có metadata; result/report chỉ sẵn sàng sau COMPLETED.

Đặt `Report API URL` thành địa chỉ API truy cập được từ trình duyệt khi chạy ngoài localhost. Export tải toàn bộ run; bảng evidence có giới hạn 10.000 dòng và bộ lọc Outcome.

## 3. Generation & Simulation

Mở `http://localhost:8080/d/paymenthub-scheduler-jobs` qua reverse proxy hoặc cùng đường dẫn trên Grafana cổng `3000`.

- Nhập Start date, Days, Records / region, Missing statements, Seconds / day và Workers ở đầu dashboard. Nhấn Enter sau khi sửa mỗi ô.
- Bấm **Generate data** rồi xác nhận để gọi `POST /generation-jobs`.
- Chờ Generation jobs hiện **COMPLETED**, sau đó bấm **Start simulation** để gọi `POST /simulation-jobs` với cùng ngày bắt đầu và số ngày.
- Hai bảng job tự refresh mỗi 5 giây qua `GET /generation-jobs` và `GET /simulation-jobs`; job mới nhất nằm trên cùng. Hiển thị RUNNING, COMPLETED, FAILED và lỗi nếu có.
- Chỉ một job chạy tại một thời điểm; submit khi đang bận trả `409`. Bảng rỗng nghĩa là chưa có job kể từ lúc scheduler khởi động. Trạng thái vẫn chỉ lưu trong bộ nhớ.

Dashboard dùng Actions và bảng có sẵn của Grafana, cùng datasource Infinity `scheduler-api`. Không cần thêm plugin. Hai nút POST gọi scheduler qua datasource ở phía Grafana server, nên hoạt động ở cả cổng `3000` và cổng proxy `8080`. Compose bật `vizActionsAuth` để Grafana 13.1 hỗ trợ kiểu Action này; khi đổi cấu hình này cần recreate service Grafana.

Khởi động scheduler và proxy theo hướng dẫn trong `README.md`. Khi thêm datasource mới, restart Grafana hoặc gọi `POST /api/admin/provisioning/datasources/reload` bằng tài khoản Grafana admin.

## Provisioning

Hai dashboard đối soát giữ nguyên UID. Dashboard mới dùng UID `paymenthub-scheduler-jobs`. Dashboard Operations thay thế dashboard Prometheus overview cũ. Datasource Infinity `reconciliation-api` dùng cho API đối soát, `scheduler-api` cho trạng thái job và datasource Prometheus cho alert.

Grafana đang chạy với bind mount sẽ tự đọc thay đổi theo chu kỳ provisioning 30 giây; không cần restart khi chỉ thay JSON. Kiểm tra `/api/dashboards/uid/paymenthub-reconciliation`, `/api/dashboards/uid/paymenthub-reconciliation-api` và `/api/datasources/uid/reconciliation-api/health` bằng tài khoản Grafana có quyền đọc.
