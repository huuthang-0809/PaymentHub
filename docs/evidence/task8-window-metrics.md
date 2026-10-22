# Task 8 — Window 1/5/15 and Core Metrics Evidence

Ngày kiểm chứng: 2026-09-26.

## Contract

- Status counts/rates giữ event-grain.
- `non_terminal_count` và pending age dùng latest state theo transaction identity.
- Pending age buckets: `<5m`, `5–<15m`, `15–<60m`, `>=60m`.
- Parse error, invalid amount và missing currency là ba signal riêng.
- `freshness_lag_seconds = max(0, computed_at - last_event_at)`.
- `dq_daily`, DQ API và late-event/watermark chưa thuộc Task 8.

## Automated verification

```text
Golden/unit suite: 180 passed, 5 skipped
PostgreSQL integration: 5 passed
Import contracts: 2 kept, 0 broken
```

Golden fixture cố định khớp toàn bộ cells cho cả ba window size. Fixture
PENDING → SUCCESS vẫn đóng góp hai event outcomes, nhưng transaction đó không
còn nằm trong latest-state pending age.

## Compose E2E

Lệnh chạy:

```bash
MONITORING_REPLAYS=2 \
MONITORING_WINDOW_SIZES=1,5,15 \
docker compose -p paymenthub-core-demo up -d --build
```

Kết quả PostgreSQL sau replay:

| Window | Rows | Tổng events | Parse errors |
|---:|---:|---:|---:|
| 1 phút | 594 | 15.000 | 1.874 |
| 5 phút | 122 | 15.000 | 1.874 |
| 15 phút | 42 | 15.000 | 1.874 |

Replay cuối: `read=15000`, `inserted=0`, `duplicated=15000`, `failed=0`,
`status=COMPLETED`. API window 15 phút trả các field mới gồm
`parse_error_count`, `non_terminal_count`, bốn pending-age buckets,
`oldest_pending_age_seconds` và `freshness_lag_seconds`.

Synthetic batch hiện tại có mọi latest transaction state là terminal, nên
`non_terminal_count=0` trong E2E 15.000 records. Golden fixture riêng kiểm chứng
non-terminal/pending-age và transition PENDING → SUCCESS.
