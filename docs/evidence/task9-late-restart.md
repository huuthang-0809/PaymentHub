# Task 9 — Late Events and Restart Safety Evidence

Ngày kiểm chứng: 2026-09-26.

## Policy demo

- Watermark scope: `(source_name, region, tenant_scope)`.
- Allowed lateness: 5 phút.
- Retention: 24 giờ.
- Các giá trị trên là fixture/demo configuration, không phải production threshold.
- Event ngoài retention không vào aggregate; metadata được ghi idempotent vào
  `late_event_audit` với reason `OUTSIDE_RETENTION`.
- Checkpoint được ghi sau data commit bằng transaction thứ hai.

## Automated verification

```text
Unit suite: 183 passed, 6 skipped
PostgreSQL integration: 6 passed, 8 warnings in 10.21s
Import contracts: 2 kept, 0 broken
Alembic head: 0010
```

PostgreSQL test chứng minh:

- Event out-of-order 10 phút được persist vào đúng event-time window và tổng
  `late_event_count=1`.
- Replay late event trả duplicate và late count vẫn bằng 1.
- Event cũ hơn watermark 25 giờ không vào canonical/aggregate và chỉ có một
  expired audit dù replay.
- Crash trước commit rollback event/aggregate và không tiến checkpoint; rerun
  insert đúng một lần.
- Crash sau commit trước checkpoint giữ event đã commit; rerun dedupe event và
  đưa checkpoint tiến đến event time, không nhân aggregate.
- Sau chuỗi lỗi/retry: 4 canonical events, aggregate total 4, late total 1,
  expired audit total 1.

## Compose compatibility

Migration trên volume demo hiện có thành công với `alembic_version=0010`.
Full-batch replay đọc 15.000 events hai lần, lượt cuối `inserted=0`,
`duplicated=15000`, `late=0`, `expired=0`, `failed=0`, `COMPLETED`.
Bốn checkpoint scope được tạo cho VN, India, Asia-ID và Asia-PH.

Mongo adapter hiện là full-batch snapshot theo `testBatchId`; checkpoint là
watermark phục vụ late classification và chẩn đoán, chưa phải resumable Mongo
offset/change-stream token.
