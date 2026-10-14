# PaymentHub — Quy trình chạy test

Tài liệu này mô tả cách chạy unit test, PostgreSQL integration test và kiểm tra
end-to-end MongoDB → PostgreSQL → API. Chạy các lệnh từ thư mục gốc repository.

## 1. Điều kiện cần

- Python 3.12 và `uv`.
- Docker Engine cùng Docker Compose.
- Các port `5432`, `27017`, `8000` không bị project khác chiếm khi chạy Compose.
- Integration test phải dùng database riêng. Không trỏ test vào database
  `paymenthub` vì test sẽ xóa và tạo lại schema `public`.

Kiểm tra nhanh:

```bash
uv --version
docker --version
docker compose version
```

## 2. Chạy unit test

```bash
uv run pytest -q
```

Kết quả tham chiếu hiện tại:

```text
183 passed, 6 skipped
```

Sáu test bị skip là PostgreSQL integration tests. Đây là hành vi mặc định để
tránh vô tình reset database.

Chạy riêng một nhóm test:

```bash
uv run pytest tests/unit/test_window_aggregator.py -q
uv run pytest tests/unit/test_process_canonical_events.py -q
uv run pytest tests/unit/test_mongo_monitoring_batch.py -q
```

Kiểm tra dependency boundary:

```bash
PYTHONPATH=src uv run lint-imports
```

Kết quả mong đợi: `2 kept, 0 broken`.

## 3. Chạy PostgreSQL integration test

### 3.1 Khởi động PostgreSQL demo

Nếu Compose demo chưa chạy:

```bash
docker compose \
  -p paymenthub-core-demo \
  up -d postgres
```

### 3.2 Tạo lại database test riêng

Các lệnh sau chỉ xóa database `paymenthub_integration`, không xóa database ứng
dụng:

```bash
docker compose \
  -p paymenthub-core-demo \
  exec -T postgres dropdb --if-exists -U paymenthub paymenthub_integration

docker compose \
  -p paymenthub-core-demo \
  exec -T postgres createdb -U paymenthub paymenthub_integration
```

### 3.3 Chạy integration suite

```bash
PAYMENTHUB_RUN_DB_INTEGRATION=1 \
TEST_DATABASE_URL='postgresql+psycopg://paymenthub:paymenthub@localhost:5432/paymenthub_integration' \
uv run pytest tests/integration -q
```

Kết quả mong đợi:

```text
15 passed
```

Các test chứng minh:

- migration chạy từ database trống, từ schema `0007` và từ `0010` có dữ liệu;
- alert schema có checks, query indexes và tối đa một `OPEN` cho mỗi dedup key;
- alert migration downgrade về `0010` mà không mất dữ liệu monitoring cũ;
- health rule state, alert và evaluation metadata commit/rollback atomically;
- concurrent evaluator và replay cùng cycle chỉ tạo một OPEN trên mỗi rule;
- notification failure xảy ra sau commit và không nhân incident;
- one-shot health runner đánh giá scope no-data, replay cùng `as_of` là no-op;
- freshness watchdog tăng consecutive failures và reset sau success;
- ba health/alert/freshness APIs khớp committed PostgreSQL và SQL row counts;
- alert cursor HMAC round-trip, tamper 422, filters/masking/timeout 503;
- cùng transaction có thể giữ PENDING và SUCCESS;
- tenant isolation và event idempotency;
- window 1/5/15 phút không ghi đè nhau;
- query window khớp dữ liệu đã commit và metric không đổi sau replay;
- late event cập nhật đúng window, expired event có audit idempotent;
- crash trước commit và sau commit trước checkpoint đều phục hồi không mất/trùng;
- replay không làm tăng aggregate;
- lỗi giữa event insert và aggregate write rollback toàn bộ;
- hai worker nhận cùng event không làm tăng count hai lần.

Dọn database test sau khi chạy:

```bash
docker compose \
  -p paymenthub-core-demo \
  exec -T postgres dropdb --if-exists -U paymenthub paymenthub_integration
```

## 4. Chạy full end-to-end bằng Compose

### 4.1 Chạy Compose

```bash
MONITORING_REPLAYS=2 \
MONITORING_WINDOW_SIZES=5 \
docker compose -p paymenthub-core-demo up -d --build
```


Dockerfile chính dùng image dependency cục bộ `paymenthub-e2e-api:latest`, nên
không tải GHCR/PyPI trong lúc build. Kiểm tra image trước khi chạy:

```bash
docker image inspect paymenthub-e2e-api:latest
```

Có thể đổi base bằng build argument `PAYMENTHUB_BASE_IMAGE`.

### 4.2 Xem trạng thái và log

```bash
docker compose \
  -p paymenthub-core-demo ps -a
```

```bash
docker compose \
  -p paymenthub-core-demo logs monitoring-batch
```

Kết quả batch mặc định mong đợi:

| Lượt | Read | Inserted | Duplicated | Failed |
|---|---:|---:|---:|---:|
| 1 | 15.000 | 15.000 | 0 | 0 |
| 2 | 15.000 | 0 | 15.000 | 0 |

Mỗi lượt phải báo 5.000 document cho VN, Asia và India. Asia là tổng của hai
tenant Asia-ID và Asia-PH.

## 5. Kiểm tra API và dữ liệu

Health API:

```bash
curl --fail http://localhost:8000/health
```

Kết quả:

```json
{"status":"ok","service":"paymenthub"}
```

Xem window aggregate qua API:

```bash
curl --fail \
  'http://localhost:8000/metrics/windows?region=ASIA&tenant_id=asia-id&window_size=5&limit=3'
```

Response gồm `data` chứa dimensions, counts, rates, amounts và window bounds.
`meta.data_as_of` là thời điểm `computed_at` mới nhất của dữ liệu đã commit.
Các giá trị rate và amount được trả dạng chuỗi để không mất precision. API sắp xếp
ổn định; Task 7 chưa mở tham số cursor hoặc sort.

Đối chiếu toàn bộ window 5 phút giữa API và PostgreSQL:

```bash
docker compose -p paymenthub-core-demo exec -T api python -c "import json,urllib.request; d=json.load(urllib.request.urlopen('http://localhost:8000/metrics/windows?window_size=5&limit=500')); print(d['meta']['count'], sum(x['total_event_count'] for x in d['data']), d['meta']['data_as_of'])"

docker compose -p paymenthub-core-demo exec -T postgres \
  psql -U paymenthub -d paymenthub -Atc \
  "SELECT count(*), sum(total_count), max(computed_at) FROM window_aggregate WHERE window_size_minutes=5;"
```

Hai lệnh phải cùng trả `122`, `15000` và cùng timestamp `data_as_of`.

Kiểm tra event và aggregate:

```bash
docker compose \
  -p paymenthub-core-demo \
  exec -T postgres psql -U paymenthub -d paymenthub -c "
    SELECT
      (SELECT count(*) FROM canonical_event) AS canonical_events,
      (SELECT count(*) FROM window_aggregate) AS aggregate_rows,
      (SELECT sum(total_count) FROM window_aggregate) AS aggregate_total;
  "
```

Với lệnh chỉ window 5 phút ở mục 4.1, kết quả tham chiếu:

```text
canonical_events = 15000
aggregate_rows   = 122
aggregate_total  = 15000
```

Kiểm tra job runs:

```bash
docker compose \
  -p paymenthub-core-demo \
  exec -T postgres psql -U paymenthub -d paymenthub -c "
    SELECT status,
           metrics->>'read' AS read,
           metrics->>'inserted' AS inserted,
           metrics->>'duplicated' AS duplicated,
           metrics->>'failed' AS failed
    FROM job_run_log
    ORDER BY started_at;
  "
```

Kiểm tra ground truth:

```bash
docker compose \
  -p paymenthub-core-demo \
  run --rm verify-data
```

Kết quả mong đợi:

```text
ground truth matched for batch=seed-20260923
```

### 5.1 Kiểm tra đủ window và metric Task 8

```bash
MONITORING_REPLAYS=2 \
MONITORING_WINDOW_SIZES=1,5,15 \
docker compose -p paymenthub-core-demo up -d --build

docker compose -p paymenthub-core-demo exec -T postgres \
  psql -U paymenthub -d paymenthub -c "
    SELECT window_size_minutes, count(*) AS rows,
           sum(total_count) AS total_events,
           sum(parse_error_count) AS parse_errors
    FROM window_aggregate
    GROUP BY window_size_minutes
    ORDER BY window_size_minutes;
  "
```

Mỗi window size phải có `total_events=15000`; số rows tham chiếu lần lượt là
594, 122 và 42. Xem evidence tại `docs/evidence/task8-window-metrics.md`.

### 5.2 Kiểm tra checkpoint và late audit Task 9

```bash
docker compose -p paymenthub-core-demo exec -T postgres \
  psql -U paymenthub -d paymenthub -c "
    SELECT source_name, region, tenant_scope, watermark
    FROM source_checkpoint
    ORDER BY source_name, region, tenant_scope;

    SELECT source_name, region, tenant_scope, event_time, watermark, reason_code
    FROM late_event_audit
    ORDER BY recorded_at DESC;
  "
```

Batch snapshot bình thường có checkpoint theo bốn scope và có thể chưa có late
audit. Kịch bản late/expired/restart được kiểm chứng bằng PostgreSQL integration
test và lưu tại `docs/evidence/task9-late-restart.md`.

## 6. Dừng và dọn môi trường

Dừng container nhưng giữ volume:

```bash
docker compose \
  -p paymenthub-core-demo down
```

Xóa cả volume demo và chạy lại từ trạng thái trống:

```bash
docker compose \
  -p paymenthub-core-demo down -v
```

`down -v` xóa toàn bộ PostgreSQL/Mongo data thuộc project
`paymenthub-core-demo`. File `tests/generated/ground_truth.json` được generator
tạo lại ở lần chạy tiếp theo.

## 7. Lỗi thường gặp

### Port đã được sử dụng

```bash
docker ps --format '{{.Names}}\t{{.Ports}}'
```

Dừng đúng Compose project đang giữ port, ví dụ:

```bash
docker compose -p paymenthub-e2e down
```

### TLS hoặc proxy khi build

Các lỗi `certificate signed by unknown authority`, `CERTIFICATE_VERIFY_FAILED`
hoặc proxy `401` là lỗi CA/proxy của Docker, không phải lỗi pipeline.
Dockerfile hiện dùng dependency image cục bộ. Giải pháp lâu dài là cấu hình
corporate CA hoặc package mirror có authentication cho Docker daemon/BuildKit.

### Integration tests vẫn bị skip

Kiểm tra đã đặt đủ cả hai biến:

```bash
export PAYMENTHUB_RUN_DB_INTEGRATION=1
export TEST_DATABASE_URL='postgresql+psycopg://paymenthub:paymenthub@localhost:5432/paymenthub_integration'
uv run pytest tests/integration -q
```

### Chạy một provider-health evaluation cycle

Sau `monitoring-batch`, chạy toàn bộ scope đã cấu hình:

```bash
PYTHONPATH=src uv run python -m bootstrap.health_evaluation \
  --database-url "$DATABASE_URL" \
  --rules-file config/health-rules.demo.json \
  --scope 'VN::ONEPAY' \
  --scope 'ASIA:asia-id:ESPAY' \
  --scope 'ASIA:asia-ph:2C2P' \
  --scope 'INDIA::RAZORPAY'
```

Có thể thêm `--as-of 2026-10-05T10:31:00Z` để chạy deterministic. Chạy lại
cùng scope và `as_of` là replay no-op. Nếu bỏ `--scope`, command đọc
`HEALTH_SCOPES`; nếu biến này cũng rỗng thì discover scope đã tồn tại trong
`window_aggregate`. Muốn freshness/absence chạy cho scope chưa có dữ liệu, phải
khai báo scope explicit.

Compose chạy service `health-evaluation` một lần sau `monitoring-batch` và chỉ
start API khi evaluation hoàn thành thành công. Đây chưa phải cron/scheduler
production.

### Kiểm tra health, alert và freshness APIs

```bash
curl --fail 'http://localhost:8000/health/providers?region=VN&provider=ONEPAY'
curl --fail 'http://localhost:8000/alerts?status=OPEN&limit=20'
curl --fail 'http://localhost:8000/_meta/freshness?region=VN&provider=ONEPAY'
```

`/alerts` trả `meta.next_cursor` khi còn trang tiếp theo; truyền nguyên giá trị
đó qua query parameter `cursor`. Cursor bị sửa, sai version hoặc sai chữ ký trả
422. Ba endpoint local Compose hiện chưa có authentication; `tenant_id` chỉ là
filter. Khi chạy ngoài local phải cấu hình `MONITORING_CURSOR_SECRET` và đặt API
sau internal authentication/authorization boundary.

## P1 W4 replay, queue, lineage và benchmark

Ingest W4 dùng RabbitMQ quorum queue với manual ACK-after-commit, retry TTL
5 giây/30 giây/5 phút và DLQ có payload đã mask. PostgreSQL queue cũ vẫn được
giữ để regression migration `0015`, nhưng không phải transport ingest chính.

Chạy 5 integration test queue cùng hard-kill/recovery test:

```bash
PAYMENTHUB_RUN_DB_INTEGRATION=1 \
TEST_DATABASE_URL='postgresql+psycopg://paymenthub:paymenthub@localhost:5432/paymenthub_integration' \
uv run pytest -q \
  tests/integration/test_postgres_queue.py \
  tests/integration/test_postgres_monitoring.py
```

Sinh lại replay/lineage benchmark cũ (lệnh reset schema database được chỉ định):

```bash
PAYMENTHUB_BENCH_DESTRUCTIVE=1 \
DATABASE_URL='postgresql+psycopg://paymenthub:paymenthub@localhost:5432/paymenthub_integration' \
PYTHONPATH=src:. uv run python tests/bench/run_w4.py
```

Artifact replay nằm tại `tests/bench/result-w4-replay.json`. `duplicated` là
duplicated output, không phải số redelivery: redelivery hợp lệ được báo riêng
bằng `redeliveries_deduplicated`. Artifact acceptance kill-container chính là
`tests/bench/result-w4.json` và được sinh bởi script bên dưới.

Chạy đúng 5 integration test RabbitMQ + PostgreSQL trên stack và volume riêng:

```bash
docker compose -p paymenthub-w4-rabbit-it \
  -f docker-compose.yml -f docker-compose.week4.yml \
  up -d --build postgres rabbitmq migrate

docker run --rm \
  --network paymenthub-w4-rabbit-it_default \
  -v "$PWD:/workspace" -w /workspace \
  -e PYTHONPATH=/workspace/src:/workspace \
  -e PAYMENTHUB_RUN_RABBITMQ_INTEGRATION=1 \
  -e TEST_DATABASE_URL=postgresql+psycopg://paymenthub:paymenthub@postgres:5432/paymenthub \
  -e RABBITMQ_TEST_URL=amqp://paymenthub:paymenthub@rabbitmq:5672/ \
  paymenthub-w4-rabbit-it-migrate:latest \
  python -m pytest -q tests/integration/test_rabbitmq_monitoring.py
```

Test reset schema của PostgreSQL trong project `paymenthub-w4-rabbit-it` và bao
phủ đúng năm trường hợp: duplicate, out-of-order, late, retry TTL và DLQ
masking/reprocess idempotent.

Chạy benchmark kill consumer tại đúng 10/25 publisher confirms:

```bash
./tests/bench/run_w4_rabbitmq.sh
```

Script dùng project/volume riêng, dừng producer tại 40%, gửi `SIGKILL` tới
`rabbitmq-consumer`, tiếp tục publish 15 message, restart consumer, đợi drain và
ghi `tests/bench/result-w4.json`. Acceptance là `publisher_confirmed =
count(distinct event_id) = 25`, `lost = 0`, `duplicated = 0`.

## P1 Week 5 — 9 health rules và quality report

```bash
uv run pytest -q \
  tests/unit/test_week5_health_rules.py \
  tests/unit/test_week5_health_report.py
PYTHONPATH=src:. uv run python tests/bench/run_w5_health_eval.py
```

Profile `WEEK5_PROVISIONAL` không được load thay production config nếu chưa có
Technical Lead approval. Kết quả committed phải là TP=18, FP=0, FN=0, TN=18,
precision=recall=1 và MTTD trung bình 460 giây. Script replay 36 timeline có nhãn
trong `tests/fixtures/monitoring/health-alerts/incidents.yaml` qua evaluator thật
và so kết quả với `tests/bench/result-w5-health.json`. Đây là acceptance fixture
synthetic, không phải ước lượng chất lượng trên traffic production.

### W5 API, Parquet và p95 khi ingest đang chạy

Khi API, RabbitMQ và consumer đang chạy, đo cả bốn endpoint trong lúc producer
vẫn publish heartbeat persistent có publisher confirms:

```bash
MONITORING_API_URL=http://localhost:8000 \
RABBITMQ_URL=amqp://paymenthub:paymenthub@localhost:5672/ \
PYTHONPATH=src uv run python tests/bench/run_w5_api.py
```

Script ghi `tests/bench/result-w5.json`. Trường
`requests_while_producer_active` chứng minh request được đo chồng lấn với ingest;
`failures` phải rỗng và `pass` phải là `true`.

Xuất bốn dataset, data dictionary và tự kiểm tra row count bằng DuckDB:

```bash
MONITORING_API_URL=http://localhost:8000 \
MONITORING_EXPORT_DIR=tests/generated/parquet \
PYTHONPATH=src uv run python -m bootstrap.export_monitoring_parquet

uv run python -c "import duckdb; print(duckdb.sql(\"SELECT count(*) FROM 'tests/generated/parquet/window_metrics.parquet'\"))"
```

Các file Parquet là output tái sinh được nên nằm trong `tests/generated`; schema,
type, nullable, source endpoint và row count nằm trong
`tests/generated/parquet/data-dictionary.json`.

### Kiểm tra SIGKILL tại 40% trước commit

```bash
PAYMENTHUB_RUN_DB_INTEGRATION=1 \
TEST_DATABASE_URL='postgresql+psycopg://paymenthub:paymenthub@localhost:5432/paymenthub_integration' \
uv run pytest -q \
  tests/integration/test_postgres_monitoring.py::test_sigkill_at_forty_percent_rolls_back_then_full_replay_is_exactly_once
```

Test xử lý 10/25 records trong subprocess rồi nhận SIGKILL. Trước resume, event,
aggregate và checkpoint đều bằng 0. Sau resume và replay lần hai, canonical=24,
aggregate total=22, checkpoint=4 và không có null lineage.

Khi cần giữ dữ liệu sau một integration test để demo bằng SQL, thêm
`PAYMENTHUB_KEEP_INTEGRATION_DB=1`. Mặc định fixture xóa schema khi teardown để
không để dữ liệu test tồn tại:

```bash
PAYMENTHUB_KEEP_INTEGRATION_DB=1 \
PAYMENTHUB_RUN_DB_INTEGRATION=1 \
TEST_DATABASE_URL='postgresql+psycopg://paymenthub:paymenthub@localhost:5432/paymenthub_integration' \
uv run pytest -q \
  tests/integration/test_postgres_monitoring.py::test_rerun_does_not_change_events_or_aggregates
```
# Watermark, arrival lag và freshness

Monitoring dùng ba tín hiệu độc lập:

- Watermark vẫn là `max(event_time)` theo source/scope, dùng cho sự kiện sai thứ tự và retention.
- RabbitMQ consumer gắn `received_at`; event là arrival-late khi
  `received_at - event_time > MONITORING_ALLOWED_LATENESS_MINUTES`.
- Producer có thể gửi heartbeat để chứng minh source vẫn hoạt động dù không có giao dịch.

Heartbeat schema v1:

```json
{
  "schema_version": 1,
  "message_type": "heartbeat",
  "source_name": "provider-gateway",
  "region": "VN",
  "tenant_id": null,
  "provider": "ONEPAY",
  "sent_at": "2026-10-12T10:05:00Z"
}
```

Ý nghĩa trạng thái freshness:

- `FRESH`: event hoặc heartbeat còn mới và dữ liệu event chưa vượt SLA.
- `IDLE`: event đã cũ nhưng heartbeat còn mới; không coi đây là lỗi.
- `UNAVAILABLE`: đã từng có heartbeat nhưng heartbeat cũng vượt SLA.
- `STALE`: source cũ chưa triển khai heartbeat và dữ liệu event vượt SLA.

Batch/replay không gắn `received_at`, do đó dữ liệu lịch sử không bị đánh dấu
arrival-late giả. Chạy migration `0016` trước khi khởi động consumer mới:

```bash
docker compose run --rm migrate alembic upgrade head
```
