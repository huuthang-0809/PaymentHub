# PaymentHub

## Chạy mô phỏng đối soát một tháng

```bash
docker compose run --build --rm month-simulation
```

Lệnh trên sinh 30 ngày từ `2026-09-01`, mỗi ngày có đủ bốn scope VN,
Asia ID, Asia PH và India. Sau khi sinh dữ liệu MongoDB, CSV và ground truth,
script gọi scheduler với ngày ảo rồi chuyển sang ngày tiếp theo sau 2 giây. Mặc
định, simulation xóa một provider statement ở ngày thứ 10 và một statement khác
ở ngày thứ 20 để mô phỏng dữ liệu provider đến thiếu mà không dừng cả tháng.

Có thể đổi nhanh cấu hình ngay trên cùng một lệnh, ví dụ:

```bash
SIMULATION_START_DATE=2026-10-01 SIMULATION_DAYS=10 \
SIMULATION_SECONDS_PER_DAY=10 SIMULATION_RECORDS_PER_REGION=2000 \
SIMULATION_MISSING_STATEMENT_COUNT=1 \
docker compose run --build --rm month-simulation
```

`SIMULATION_MISSING_STATEMENT_COUNT` nhận `0`, `1` hoặc `2`; đặt `0` để chạy đủ
mọi provider statement.

Các CSV và ground truth được ghi vào `tests/generated/month-simulation`.
