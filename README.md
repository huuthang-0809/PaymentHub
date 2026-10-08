# PaymentHub

## Chạy mô phỏng đối soát một tháng

```bash
export SIMULATION_START_DATE=2026-09-01 SIMULATION_DAYS=30
export RECON_STATEMENT_DIR=./tests/generated/month-simulation/provider-statements

docker compose run --build --rm month-simulation-generate
docker compose --profile scheduler up --build \
  reconciliation-scheduler prometheus alertmanager
```

Lệnh đầu sinh MongoDB, CSV và ground truth. Lệnh thứ hai chạy scheduler thật với
các ngày ảo, nên hai giai đoạn không cùng tiêu thụ RAM và metric vẫn được export
tại cổng `9101`. Scheduler giữ process sau ngày cuối để Prometheus tiếp tục đọc
metric; nhấn `Ctrl+C` sau khi kiểm tra alert.

Simulation trên sinh 30 ngày từ `2026-09-01`, mỗi ngày có đủ bốn scope VN,
Asia ID, Asia PH và India. Nó xóa một provider statement ở ngày thứ 10 và một
statement khác ở ngày thứ 20 để mô phỏng dữ liệu provider đến thiếu.
Timestamp của mỗi batch được phân bố đều trong đúng business period 24 giờ nên
dữ liệu của một ngày không tràn sang reconciliation run ngày kế tiếp.

Có thể đổi nhanh cấu hình ngay trên cùng một lệnh, ví dụ:

```bash
docker volume rm payment-intern_postgres_data
docker compose up -d --build

export SIMULATION_START_DATE=2026-10-01 SIMULATION_DAYS=5
export SIMULATION_SECONDS_PER_DAY=10 SIMULATION_RECORDS_PER_REGION=200000
export SIMULATION_MISSING_STATEMENT_COUNT=1
export RECON_STATEMENT_DIR=./tests/generated/month-simulation/provider-statements

docker compose run --rm --build month-simulation-generate
docker compose --profile scheduler up -d --build
```

`SIMULATION_MISSING_STATEMENT_COUNT` nhận `0`, `1` hoặc `2`; đặt `0` để chạy đủ
mọi provider statement.

Ground truth chi tiết mặc định không được tạo để tránh file JSON nhiều GB. Chỉ
bật khi cần kiểm chứng oracle:

```bash
export SIMULATION_WRITE_GROUND_TRUTH=true
```

Các CSV được ghi vào `tests/generated/month-simulation/provider-statements`.

API nhận statement bằng `202 Accepted` rồi xử lý trong background. Simulation
được chạy bởi chính scheduler và poll trạng thái run thay vì giữ HTTP request
đến khi matching hoàn tất. Không đặt `SIMULATION_START_DATE` thì cùng scheduler
tự động dùng thời gian thực. Có thể
điều chỉnh `RECON_STATUS_POLL_SECONDS` (mặc định `1`) và
`RECON_STATUS_TIMEOUT_SECONDS` (mặc định `3600`); `RECON_API_TIMEOUT_SECONDS`
chỉ là timeout cho từng request upload/poll, không phải timeout xử lý toàn run.
