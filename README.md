# PaymentHub

## Generate data và chạy simulation qua API

Service `reconciliation-scheduler` cung cấp API riêng. Nginx chuyển tiếp
`/generation-jobs` và `/simulation-jobs` đến scheduler; các đường dẫn còn lại
đến Grafana. Service `api` tiếp tục xử lý đối soát như trước.

Khởi động:

```bash
# Dùng cùng thư mục cho generator và scheduler.
export RECON_STATEMENT_DIR=./tests/generated/month-simulation/provider-statements
mkdir -p "$RECON_STATEMENT_DIR"

# Cho scheduler quyền ghi vào bind mount trên Linux.
export SCHEDULER_UID=$(id -u) SCHEDULER_GID=$(id -g)
docker compose --profile scheduler up -d --build
```

Mở Grafana qua `http://localhost:8080`. Có thể đổi cổng bằng `PROXY_PORT`.
Khi dùng domain hoặc cổng khác, đặt `GRAFANA_ROOT_URL` thành URL truy cập bên ngoài.
`RECON_SCHEDULES_JSON` trong `.env` phải chứa các lịch đối soát cần chạy.
Scheduler chờ request, không tự chạy simulation khi khởi động.

### 1. Generate data

```bash
curl -X POST http://localhost:8080/generation-jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "start_date": "2026-10-01",
    "days": 5,
    "records_per_region": 200000,
    "missing_statement_count": 1
  }'
```

Response `202 Accepted`:

```json
{"job_id":"<generation-job-id>","status":"running","error":null}
```

Kiểm tra trạng thái:

```bash
curl http://localhost:8080/generation-jobs/<generation-job-id>
```

Generator ghi dữ liệu vào MongoDB và CSV vào `RECON_STATEMENT_DIR`.
`missing_statement_count` nhận `0`, `1` hoặc `2`, không vượt quá `days`;
đặt `0` để giữ đủ provider statements. `records_per_region` tối thiểu là `2`
để cả hai tenant Asia đều có dữ liệu. API không ghi ground truth chi tiết.

### 2. Chạy simulation sau khi generate completed

```bash
curl -X POST http://localhost:8080/simulation-jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "start_date": "2026-10-01",
    "days": 5,
    "seconds_per_day": 10,
    "worker_count": 2
  }'
```

Kiểm tra trạng thái:

```bash
curl http://localhost:8080/simulation-jobs/<simulation-job-id>
```

Mỗi simulation tạo một `DailyReconciliationScheduler` mới, đọc CSV đã sinh,
gọi service `api` và chờ từng reconciliation run kết thúc.
`worker_count` là số scope đối soát tối đa chạy đồng thời.
`seconds_per_day` là khoảng chờ giữa các ngày, ngoài thời gian đối soát.

Trạng thái job có ba giá trị: `running`, `completed`, `failed`.
Khi lỗi, trường `error` chứa thông tin lỗi. `completed` nghĩa là xử lý hết
các ngày; statement thiếu được bỏ qua và ghi log/metric như scheduler cũ,
để vẫn chạy được kịch bản mô phỏng thiếu dữ liệu. Reconciliation run bị lỗi
hoặc timeout sẽ khiến simulation có trạng thái `failed`.

Chỉ một generation hoặc simulation job được chạy tại một thời điểm;
submit khi đang bận trả `409`. Trạng thái lưu trong bộ nhớ và mất khi restart.
Chạy một process Uvicorn (`--workers 1`) và một replica scheduler.
Metrics vẫn được Prometheus đọc tại `reconciliation-scheduler:9101/metrics/`.

Qua Grafana ở cổng proxy, dùng các URL cùng origin cho Actions POST và query GET.
Nginx hiện chỉ định tuyến; các endpoint job chưa có xác thực, nên chỉ truy cập
qua mạng tin cậy hoặc lớp xác thực của môi trường triển khai.

## Chạy generator và scheduler bằng CLI

Generator CLI vẫn nhận các biến môi trường cũ:

```bash
export SIMULATION_START_DATE=2026-10-01 SIMULATION_DAYS=5
export SIMULATION_RECORDS_PER_REGION=200000 SIMULATION_MISSING_STATEMENT_COUNT=1
export RECON_STATEMENT_DIR=./tests/generated/month-simulation/provider-statements

docker compose run --rm --build month-simulation-generate
```

CLI này sinh dữ liệu trước, không submit API job. Có thể gọi
`POST /simulation-jobs` sau khi generate xong. Muốn ghi ground truth chi tiết,
đặt `SIMULATION_WRITE_GROUND_TRUTH=true` trước khi chạy generator CLI.

Entry point cũ `python -m bootstrap.scheduler` vẫn có thể chạy trực tiếp:
không đặt `RECON_SIMULATION_START_DATE` thì chạy lịch theo thời gian thực;
có đặt thì chạy mô phỏng theo biến môi trường. Không chạy CLI song song với
API job vì hai chế độ không chia sẻ khóa trong bộ nhớ.
