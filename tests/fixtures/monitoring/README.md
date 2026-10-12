# Monitoring golden fixture (T1)

Đây là fixture canonical viết tay, độc lập với regional adapters, synthetic
generator và `WindowAggregator`. `events.jsonl` là input; `expected-1m.json`, `expected-5m.json` và `expected-15m.json` là
đáp án cố định cho contract T0. Không dùng production code để sinh expected.

## Cách đọc

- Có 25 input records nhưng chỉ 24 `event_id`: record thứ 9 replay `e01` và bị dedupe.
- `e07` deleted và `e08` test-data chỉ phục vụ audit, không vào aggregate.
- Còn 22 base events, phân thành 10 rows bởi window và dimensions.
- `e12` lúc `10:04:59Z` thuộc `[10:00,10:05)`; `e13` lúc `10:05:00Z`
  thuộc `[10:05,10:10)`.
- `e14.updated_at=null`, nên dùng `created_at=10:06:00Z`.
- `e10/e11` là hai event khác nhau của cùng transaction: PENDING rồi SUCCESS. Event-grain giữ cả hai, latest-state pending-age chỉ lấy SUCCESS nên không tính transaction này là pending.
- `e17/e18` có cùng `txn_id=shared-txn` nhưng khác Asia tenant và currency.
- `e15/e16` là CARD_LINK/TOKENIZATION: có trong total nhưng không thuộc operation.

## Phép tính row chính

Row VN/ONEPAY/PAYMENT/CARD/VND `[10:00,10:05)` có 12 base events:

| Bucket | Event IDs | Count |
|---|---|---:|
| Success | e01, e02, e06, e11, e12 | 5 |
| Failed | e03, e21, e22 | 3 |
| Pending | e04, e10, e23 | 3 |
| Unknown | e05 | 1 |

Do đó `operation_event_count=12`, `success_rate=5/12=0.416667`,
`failed_rate=3/12=0.250000`, `unknown_rate=1/12=0.083333`.

Chỉ e01, e02, e11 và e12 monetary-eligible. Vì vậy
`monetary_count=4` và amount là `100+200+400+50=750 VND`. e06 vẫn là
success event nhưng `amount_status=INVALID`, nên tăng `invalid_amount_count`
và không cộng tiền.

Row cùng dimensions ở `[10:05,10:10)` có e13 SUCCESS và e14 PENDING:
`success_rate=1/2`, monetary amount `60 VND`. Các row còn lại mỗi row một event,
nên reviewer có thể đối chiếu trực tiếp với `expected-5m.json`.

## Quy ước expected

- Rates được lưu thành chuỗi decimal 6 chữ số để tránh sai số JSON float.
- Pending age tại `computed_at=11:00Z` dùng `created_at`; các bucket là `<5m`, `5–<15m`, `15–<60m`, `>=60m`.
- `freshness_lag_seconds = max(0, computed_at - last_event_at)`.
- `e06` mang parse-error signal và tăng `parse_error_count` cùng `invalid_amount_count`.
- Rate là `null` nếu `operation_event_count=0`.
- Null dimension được persist bằng chuỗi rỗng `""`.
- Refund amount là gross và nằm ở row `txn_type=REFUND`; đây là rule demo T0
  đang chờ nghiệp vụ xác nhận.
