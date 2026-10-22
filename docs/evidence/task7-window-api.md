# Task 7 — Window Metrics API Evidence

Ngày kiểm chứng: 2026-09-26.

## Phạm vi

Pipeline Compose chạy MongoDB → canonical event → PostgreSQL window aggregate →
`GET /metrics/windows`, với `MONITORING_REPLAYS=2` và window 5 phút.

## Lệnh chạy

```bash
MONITORING_REPLAYS=2 \
MONITORING_WINDOW_SIZES=5 \
docker compose -p paymenthub-core-demo up -d --build

curl --fail \
  'http://localhost:8000/metrics/windows?window_size=5&limit=500'

docker compose -p paymenthub-core-demo exec -T postgres \
  psql -U paymenthub -d paymenthub -Atc \
  "SELECT count(*), sum(total_count), max(computed_at) FROM window_aggregate WHERE window_size_minutes=5;"
```

## Kết quả quan sát

| Kiểm tra | API | PostgreSQL |
|---|---:|---:|
| Số aggregate rows | 122 | 122 |
| Tổng `total_event_count` | 15.000 | 15.000 |
| `data_as_of` / `max(computed_at)` | `2026-09-26T10:13:07.405386Z` | `2026-09-26 10:13:07.405386+00` |

Lượt replay cuối đọc 15.000 documents, map 15.000, `inserted=0`,
`duplicated=15.000`, `failed=0`, trạng thái `COMPLETED`. Sau replay API vẫn trả
122 rows và tổng 15.000 events.

Integration suite chạy trong Docker network để tránh giới hạn port forwarding của
môi trường WSL:

```text
5 passed, 7 warnings in 7.41s
```

Unit suite và dependency boundaries được kiểm tra riêng theo
[`docs/testing-guide.md`](../testing-guide.md).
