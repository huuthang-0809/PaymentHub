# Provider Health Rule Catalog — W4-T0

Ngày cập nhật: 05/10/2026. Owner: P1.

## Trạng thái contract

Technical Design yêu cầu 9 rule production nhưng repository chưa chứa contract
định danh, công thức hay threshold đã được phê duyệt. Vì vậy tại W4-T0 chưa có
rule production nào được phép công bố là hoàn thành. Bảng dưới là checklist nhận
contract, không phải danh sách rule do P1 tự đặt.

| Slot | Rule ID | Metric/formula | Min sample | Breach/recovery | Impact | Cooldown | No-data | Runbook | Status |
|---:|---|---|---|---|---|---|---|---|---|
| 1 | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | BLOCKED_CONTRACT |
| 2 | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | BLOCKED_CONTRACT |
| 3 | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | BLOCKED_CONTRACT |
| 4 | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | BLOCKED_CONTRACT |
| 5 | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | BLOCKED_CONTRACT |
| 6 | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | BLOCKED_CONTRACT |
| 7 | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | BLOCKED_CONTRACT |
| 8 | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | BLOCKED_CONTRACT |
| 9 | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | `[Need Confirm]` | BLOCKED_CONTRACT |

Một slot chỉ chuyển sang `APPROVED` khi có đủ `rule_id` ổn định/version, source
metric, scope filters, window/lookback, công thức/toán tử, threshold, minimum
sample, consecutive breach/recovery, health impact/severity, cooldown, no-data
behavior và runbook owner/reference.

## Demo rules được phép dùng để phát triển engine

Các rule sau chỉ dùng cho fixture/test và phải có prefix `DEMO_`. Threshold là
input của fixture/config, không phải default production.

| Rule ID | Input và công thức | Minimum sample | No-data | Mục đích test |
|---|---|---|---|---|
| `DEMO_LOW_SUCCESS_RATE_V1` | `sum(success_count) / sum(operation_event_count) < threshold`; không lấy trung bình rate | Config; denominator 0 là insufficient | `INSUFFICIENT_DATA` | Rate, min sample, consecutive breach/recovery |
| `DEMO_HIGH_FAILURE_RATE_V1` | `sum(failed_count) / sum(operation_event_count) >= threshold` | Config; denominator 0 là insufficient | `INSUFFICIENT_DATA` | Nhiều reason và precedence |
| `DEMO_STALE_DATA_V1` | `evaluated_at - data_as_of > max_lag` | Không áp dụng | Có thể BREACH sau `max_lag` | Absence/freshness khi không có event mới |

Demo config phải khai báo explicit: window size/lookback, threshold,
minimum_sample, breach/recovery windows, impact/severity, cooldown và runbook
fixture. Không load demo rules trong production profile.

## Yêu cầu xác nhận gửi Technical Lead

1. Cung cấp tên và ý nghĩa chính xác của 9 rule bắt buộc.
2. Với mỗi rule, điền đầy đủ các cột contract nêu trên.
3. Xác nhận freshness/absence có nằm trong 9 rule hay là rule bổ sung.
4. Xác nhận callback, polling, queue và DLQ rule có bị hoãn vì metric đầu vào
   chưa tồn tại hay cần mở rộng aggregate trong tuần 4.
5. Xác nhận enum public dùng `UNHEALTHY`, không dùng `UNAVAILABLE`.

## Week 5 provisional acceptance profile

`config/health-rules.week5-provisional.yaml` định nghĩa đúng 9 nhóm rule bắt buộc:
volume baseline, failure rate, freshness, data quality, queue/DLQ và bốn rule
pending đặc thù Asia/India. Mỗi rule khai báo
minimum sample, window, consecutive breach/recovery, impact/severity, cooldown và
runbook. Profile này hoàn thành acceptance checklist về engine/lifecycle nhưng
vẫn mang status `AWAITING_TECHNICAL_LEAD_APPROVAL`; bảng 9 production slots phía
trên chưa được đổi thành `APPROVED`.

Lifecycle của cả 9 rule được khóa bởi
`tests/unit/test_week5_health_rules.py`; báo cáo số nằm tại
`tests/bench/result-w5-health.json` và
`docs/evidence/p1-week5/precision-recall-mttd.md`.
