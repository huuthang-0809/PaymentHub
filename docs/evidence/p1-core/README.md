# P1 core — M1/M2/M3 evidence

Ngày kiểm chứng: 2026-09-26. Các số dưới đây là kết quả thực, không phải dữ liệu dự kiến.

## Kết quả theo mốc

| Mốc | Bằng chứng | Kết quả |
|---|---|---|
| M1 | Golden fixture và CLI replay | Fixture 25 records khớp expected 1/5/15 phút; replay không đổi aggregate |
| M2 | Mongo → PostgreSQL → API | 15.000 events; window 5 phút có 122 rows/tổng 15.000; replay lần hai inserted=0, duplicated=15.000 |
| M3 | DB/volume trống, migration 0010, ba window và checkpoint | Window 1/5/15 có 594/122/42 rows, mỗi cỡ tổng 15.000; 4 checkpoint scopes; API ready |

Chi tiết M2: [Task 7 evidence](../task7-window-api.md). Chi tiết metrics M3:
[Task 8 evidence](../task8-window-metrics.md). Late/restart:
[Task 9 evidence](../task9-late-restart.md).

## Test tự động

```text
Unit/default suite:      183 passed, 6 skipped
PostgreSQL integration:  6 passed, 8 warnings
Import contracts:        2 kept, 0 broken
```

Integration tests dùng database riêng và kiểm tra migration từ trống, event
identity, transaction rollback, concurrency, replay, late/expired event và
restart quanh commit/checkpoint.

## Compose từ volume trống

```bash
docker compose -p paymenthub-m3 down -v --remove-orphans
MONITORING_REPLAYS=2 \
MONITORING_WINDOW_SIZES=1,5,15 \
docker compose -p paymenthub-m3 up -d --build
docker compose -p paymenthub-m3 wait monitoring-batch
```

Quan sát thực tế:

```text
alembic_version = 0010
canonical_event = 15000
window 1m  = 594 rows, total_count = 15000, late_event_count = 0
window 5m  = 122 rows, total_count = 15000, late_event_count = 0
window 15m = 42 rows,  total_count = 15000, late_event_count = 0
run 1 = COMPLETED, inserted = 15000, duplicated = 0
run 2 = COMPLETED, inserted = 0, duplicated = 15000
source_checkpoint = 4 scopes
late_event_audit = 0 rows (batch này không chứa event ngoài retention)
GET /health/ready = {"status":"ready"}
```

`verify-data`, `migrate`, `monitoring-batch` đều exit code 0. Late/expired cases
được chứng minh riêng bằng PostgreSQL integration tests vì synthetic batch chuẩn
không cố tình chứa event ngoài retention.

## EXPLAIN trước/sau index

Hai query và DDL: [explain-index-comparison.sql](explain-index-comparison.sql).
Raw plans: [before](explain-before.txt), [after](explain-after.txt).

| Query | Trước index | Sau index | Thay đổi |
|---|---:|---:|---:|
| Q1 scope/status theo event date | 26.367 ms; shared hit=4405 | 20.454 ms; shared hit=6, read=111 | runtime giảm 22,4%; chuyển sang index-only scans |
| Q2 latest provider updates | 22.381 ms; shared hit=4653 | 0.861 ms; shared hit=109, read=29 | runtime giảm 96,2%; dùng provider/updated_at index |

Đây là một lần đo `EXPLAIN (ANALYZE, BUFFERS)` trên dataset đại diện; cache,
phần cứng và cardinality ảnh hưởng runtime nên không coi bảng này là benchmark SLA.

## Contract cần giải thích khi bàn giao

- Event identity khác transaction identity: ưu tiên trusted `event_id`; fallback
  SHA-256 versioned trên region, tenant scope, txn_id, nguồn thời gian, event time UTC và status.
- Tumbling window là `[start, end)`, dùng `updated_at` hoặc fallback `created_at`,
  vì vậy event đúng boundary thuộc window kế tiếp.
- Success/failed/unknown rate dùng `operation_event_count`; denominator bằng 0
  trả `null`. Monetary metrics chỉ nhận money movement có amount/currency hợp lệ.
- Canonical insert và aggregate upsert commit cùng một transaction. Checkpoint
  chỉ được ghi sau commit; crash trước commit rollback, crash sau commit replay
  dựa vào idempotency nên không nhân đôi.

## Known limitations

- Mongo source hiện replay full snapshot, chưa resume theo offset/change stream.
- Authentication/authorization, cursor pagination, provider health/alerts,
  general retry/DLQ, `dq_daily`, DQ API, HA và production SLA chưa triển khai.
- Allowed lateness/retention hiện là cấu hình demo, chưa phải threshold production.
- Fallback identity có thể gộp hai update thật nếu source không có trusted ID và
  chúng trùng transaction, status cùng microsecond; source sequence vẫn cần xác nhận.
