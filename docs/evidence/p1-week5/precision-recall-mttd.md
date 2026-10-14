# P1 Week 5 — Precision, Recall and MTTD report

Ngày chạy: 13/10/2026. Profile: `WEEK5_PROVISIONAL`.

## Dataset và phương pháp

- Fixture `tests/fixtures/monitoring/health-alerts/incidents.yaml` chứa 36 timeline
  đã gán nhãn: 9 rule, mỗi rule có 2 incident và 2 normal case.
- Có incident mẫu provider X failure 40% và volume về 0 lúc 14:00. Script đọc
  observation có timestamp từ fixture và replay qua `HealthEvaluator`/`MetricRule`
  thật, bao gồm consecutive breach và state của lần đánh giá trước.
- Precision = `TP/(TP+FP)`; recall = `TP/(TP+FN)`.
- MTTD của mỗi incident tính từ `start` đã gán nhãn tới timestamp đánh giá đầu
  tiên trả về `breach_confirmed`; không suy ra trực tiếp từ threshold.
- Script: `tests/bench/run_w5_health_eval.py`.
- Machine-readable result: `tests/bench/result-w5-health.json`.

## Kết quả

| TP | FP | FN | TN | Precision | Recall | MTTD trung bình |
|---:|---:|---:|---:|---:|---:|---:|
| 18 | 0 | 0 | 18 | 1.0000 | 1.0000 | 460 giây (7.67 phút) |

MTTD từng rule nằm trong artifact JSON: 360 giây với rule xác nhận sau một
observation và 660 giây với rule yêu cầu hai consecutive breach.

## Lifecycle acceptance

`tests/unit/test_week5_health_rules.py` chạy từng rule độc lập và chứng minh:
OPEN chỉ sau đủ consecutive breach; continued breach UPDATE cùng alert; cooldown
không notify lặp; replay cùng timestamp là no-op; đủ recovery window RESOLVE đúng
alert ID. Tổng cộng đúng 9 unique versioned rules và mọi rule có runbook.

## Giới hạn diễn giải

Đây là tập timeline synthetic có nhãn và cân bằng nên precision/recall 100% chỉ
là acceptance result, không phải ước lượng production. Profile và threshold đang
`AWAITING_TECHNICAL_LEAD_APPROVAL`; cần labeled traffic production/reviewed
incident set trước khi công bố KPI vận hành.
