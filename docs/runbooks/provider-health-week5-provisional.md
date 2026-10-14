# Runbook — Week 5 provisional provider-health rules

Status: `WEEK5_PROVISIONAL`, awaiting Technical Lead approval. Các threshold dưới
đây phục vụ acceptance test/benchmark, không phải SLA production. Mỗi incident
được dedupe theo `(rule_id, rule_version, region, tenant_scope, provider)`;
cooldown chỉ chặn notification lặp; recovery đóng đúng alert đang mở.

## Quy trình chung

1. Kiểm tra `/_meta/freshness` và runner trước khi kết luận provider lỗi.
2. Đối chiếu scope, closed window, sample, numerator/denominator và `data_as_of`.
3. Loại trừ test/deleted data, clock skew và mapping deployment mới.
4. Không tự retry giao dịch tiền; escalation theo owner/provider đã phê duyệt.
5. Chỉ đóng khi đủ `recovery_windows`; xác minh cùng alert ID thành `RESOLVED`.

## 1 W5_VOLUME_DROP_V1

Volume cửa sổ 15 phút `<20%` trung bình cùng thứ và giờ trong 28 ngày trước,
hai lần liên tiếp. Nếu chưa có baseline thì `INSUFFICIENT_DATA`. Kiểm tra lịch nghỉ,
heartbeat và thay đổi routing trước khi kết luận provider lỗi.

## 2 W5_FAILURE_RATE_V1

Failure rate `>=20%` với ít nhất 100 operation trong cửa sổ 5 phút, hai lần
liên tiếp. Kiểm tra provider error code và mapping terminal status. Sample nhỏ
không tạo breach.

## 3 W5_STALE_DATA_V1

`evaluated_at - data_as_of >600s`. Đối chiếu heartbeat: heartbeat còn mới nhưng
không có giao dịch là `IDLE`, không phải outage; heartbeat cũng cũ mới escalation.

## 4 W5_DATA_QUALITY_V1

Có ít nhất một `UNKNOWN` hoặc `amount_status=INVALID` trong cửa sổ 5 phút.
Kiểm tra raw đã mask, schema/version producer và mapping deployment gần nhất.

## 5 W5_QUEUE_PRESSURE_V1

Queue message READY/PROCESSING già nhất quá 30 giây hoặc DLQ vượt 30 message.
Kiểm tra consumer, PostgreSQL, retry queue và error class trước khi reprocess DLQ.

## 6 W5_ASIA_QR_VA_PENDING_24H_V1

Asia QR hoặc Virtual Account ở CREATED/PENDING quá 86.400 giây. Truy vấn trạng
thái mới nhất theo transaction, không đếm transition lịch sử. Xác minh expiry và
callback provider trước khi can thiệp.

## 7 W5_ASIA_REFUND_SHAPE_B_PENDING_V1

Asia REFUND có `refund_of` (Shape B) ở CREATED/PENDING quá 3.600 giây. Kiểm tra
parent payment, provider reference và callback; không tự retry giao dịch tiền.

## 8 W5_INDIA_NON_TERMINAL_AGE_V1

India CREATED/PENDING/UNKNOWN quá 1.800 giây. Kiểm tra webhook, poller và trạng
thái provider trước escalation.

## 9 W5_INDIA_REFUND_CREATED_AGE_V1

India REFUND còn CREATED quá 900 giây. Kiểm tra refund reference, original
payment và idempotency key; recovery khi không còn refund vượt tuổi.
