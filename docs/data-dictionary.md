# PaymentHub — Data & Metrics Dictionary

## 1. Quy ước và nguồn chuẩn

- Domain canonical: [model.py](../src/canonical/domain/model.py); đối soát: [models.py](../src/reconciliation/domain/models.py).
- Nghiệp vụ matching: [services.py](../src/reconciliation/domain/services.py); báo cáo: [summary.py](../src/reconciliation/application/summary.py).
- Schema database: [migrations](../migrations/versions); HTTP: [http.py](../src/reconciliation/adapters/http.py).
- `null` là thiếu/không áp dụng/không đủ bằng chứng theo từng trường, không mặc định bằng `0`.
- Kiểu trong bảng domain là annotation Python, không cam kết constructor kiểm tra mọi annotation. Nullable là cho phép `None`; cột có default vẫn có thể không nullable.
- Tiền xử lý bằng Decimal và integer, không dùng float. PostgreSQL dùng `numeric(38,0)`; số quá 38 chữ số không nằm trong hợp đồng lưu trữ/export.
- Timestamp canonical phải timezone-aware, chuẩn hóa UTC. `business_date` là ngày địa phương, không phải ngày UTC.

| Currency | Exponent | Ví dụ major → minor |
|---|---:|---|
| VND | 0 | 100000 → 100000 |
| IDR | 0 | 100000 → 100000 |
| INR | 2 | 12.34 → 1234 |
| PHP | 2 | 12.34 → 1234 |
| USD | 2 | 12.34 → 1234 |

Không tự làm tròn số vượt precision. Currency ngoài bảng bị Money từ chối. Money tự thân không cấm số âm; parser provider cấm amount âm.

## 2. Enum và phạm vi định danh

- `Region`: VN, ASIA, INDIA.
- `TxnType`: PAYMENT (thanh toán), LOAD (nạp), REFUND (hoàn), CARD_LINK (liên kết thẻ), TOKENIZATION (tạo token), UNKNOWN.
- `CanonicalStatus`: CREATED, PENDING, SUCCESS, FAILED, REFUNDED, PARTIAL_REFUNDED, EXPIRED, CANCELLED, UNKNOWN. Có enum không đồng nghĩa mọi adapter sinh giá trị đó.
- `AmountStatus`: OK (đã đọc hợp lệ), ASSUMED_CURRENCY (currency từ cấu hình), NO_CURRENCY (thiếu), INVALID (sai tiền/currency), NOT_APPLICABLE (không áp dụng).
- `Instrument`: CARD, VIRTUAL_ACCOUNT, EWALLET, QR, PAYMENT_LINK, DIRECT_DEBIT.
- Khóa global system: `(region, tenant_id, txn_id)`.
- Scope matching: `(region, tenant_id, provider, currency)`; riêng chẩn đoán lệch currency dùng exact ID trong `(region, tenant_id, provider)`.

## 3. CanonicalTransaction

| Trường | Kiểu Python | Nullable | Ý nghĩa / quy tắc |
|---|---|---|---|
| `region` | `Region` | Không | Khu vực nguồn; VN, ASIA hoặc INDIA. |
| `tenant_id` | `str  /  None` | Có | Tenant nguồn; bắt buộc với ASIA, cấu hình hiện có asia-id/asia-ph; VN/INDIA dùng null. |
| `txn_id` | `str` | Không | ID giao dịch nguồn, chuyển thành chuỗi; duy nhất trong (region, tenant_id). |
| `txn_type` | `TxnType` | Không | Loại nghiệp vụ canonical; xem enum và mapping nguồn. |
| `provider` | `str` | Không | Mã provider chuẩn hóa trim/uppercase; ONEPAY, ESPAY, 2C2P, RAZORPAY theo cấu hình hiện tại. |
| `status` | `CanonicalStatus` | Không | Trạng thái giao dịch; canonical dùng enum, provider giữ chuỗi nguồn. Trạng thái run được mô tả riêng. |
| `amount_status` | `AmountStatus` | Không | Mức hợp lệ của tiền: OK, ASSUMED_CURRENCY, NO_CURRENCY, INVALID, NOT_APPLICABLE. |
| `created_at` | `datetime` | Không | Thời điểm tạo giao dịch nguồn, timezone-aware và chuẩn hóa UTC. |
| `is_terminal` | `bool` | Không | Cờ trạng thái kết thúc theo adapter; không đồng nghĩa giao dịch thành công. |
| `is_money_movement` | `bool` | Không | Cờ loại/trạng thái có dịch chuyển tiền theo adapter; không phải điều kiện lọc duy nhất của matching. |
| `is_reconcilable` | `bool` | Không | Cờ đủ điều kiện loại nghiệp vụ để đối soát; engine kiểm tra thêm amount_status. |
| `instrument` | `Instrument  /  None` | Có | Phương tiện thanh toán đã map; null nếu chưa có mapping. |
| `provider_txn_id` | `str  /  None` | Có | ID giao dịch provider; khóa exact ưu tiên 1. Refund ASIA loại bỏ ID kế thừa parent khi matching. |
| `provider_ref_id` | `str  /  None` | Có | Tham chiếu provider; exact ưu tiên 2 chỉ áp dụng ASIA. |
| `merchant_ref` | `str  /  None` | Có | Tham chiếu merchant; exact ưu tiên 3. |
| `order_ids` | `tuple[str, ...]` | Không | Danh sách ID bill/source liên quan; tuple rỗng khi không có. |
| `channel_raw` | `str  /  None` | Có | Kênh/phương thức thanh toán nguyên bản để truy vết. |
| `merchant_id` | `str  /  None` | Có | ID merchant; dùng trong fuzzy matching, phải khác null. |
| `provider_merchant_id` | `str  /  None` | Có | Mã merchant ở phía provider. |
| `service_id` | `str  /  None` | Có | ID hoặc loại dịch vụ nguồn. |
| `customer_id` | `str  /  None` | Có | ID khách hàng nguồn; không được export trong ReconResult. |
| `amount_minor` | `int  /  None` | Có | Số nguyên đơn vị tiền nhỏ nhất; phải đọc cùng currency. Provider không nhận số âm. |
| `currency` | `str  /  None` | Có | Mã tiền viết hoa; xem bảng exponent. Không cộng chéo currency. |
| `refund_of` | `str  /  None` | Có | Tham chiếu parent của refund; ý nghĩa khóa khác nhau theo region. |
| `refunded_amount_minor` | `int  /  None` | Có | Tổng tiền đã refund ghi nhận trên parent, theo currency system; không cộng thêm vào tiền giao dịch. |
| `updated_at` | `datetime  /  None` | Có | Thời điểm cập nhật nguồn, UTC. |
| `paid_at` | `datetime  /  None` | Có | Thời điểm thanh toán nguồn, UTC. |
| `provider_created_at` | `datetime  /  None` | Có | Thời điểm tạo phía provider, UTC. |
| `failure_reason` | `str  /  None` | Có | Lý do thất bại từ nguồn. |
| `callback_attempts` | `int` | Không | Số lần callback; VN/ASIA chỉ là chỉ báo 0/1 từ IsSentCallback, INDIA theo log/attempts. |
| `provider_poll_count` | `int  /  None` | Có | Số lần poll/reconcile nguồn; null nếu thiếu. |
| `is_deleted` | `bool` | Không | Cờ xóa; RunMatching loại khỏi input system. |
| `is_test_data` | `bool` | Không | Cờ dữ liệu test được adapter nhận diện; RunMatching loại khỏi input system. |
| `raw` | `dict[str, Any]` | Không | Payload nguồn phục vụ lưu/truy vết; không trả qua API query hoặc export kết quả. |

## 4. ProviderTransaction — sao kê chuẩn hóa

| Trường | Kiểu Python | Nullable | Ý nghĩa / quy tắc |
|---|---|---|---|
| `provider` | `str` | Không | Mã provider chuẩn hóa trim/uppercase; ONEPAY, ESPAY, 2C2P, RAZORPAY theo cấu hình hiện tại. |
| `region` | `Region` | Không | Khu vực nguồn; VN, ASIA hoặc INDIA. |
| `tenant_id` | `str  /  None` | Có | Tenant nguồn; bắt buộc với ASIA, cấu hình hiện có asia-id/asia-ph; VN/INDIA dùng null. |
| `provider_txn_id` | `str  /  None` | Có | ID giao dịch provider; khóa exact ưu tiên 1. Refund ASIA loại bỏ ID kế thừa parent khi matching. |
| `provider_ref_id` | `str  /  None` | Có | Tham chiếu provider; exact ưu tiên 2 chỉ áp dụng ASIA. |
| `merchant_ref` | `str  /  None` | Có | Tham chiếu merchant; exact ưu tiên 3. |
| `merchant_id` | `str  /  None` | Có | ID merchant; dùng trong fuzzy matching, phải khác null. |
| `amount_minor` | `int` | Không | Số nguyên đơn vị tiền nhỏ nhất; phải đọc cùng currency. Provider không nhận số âm. |
| `currency` | `str` | Không | Mã tiền viết hoa; xem bảng exponent. Không cộng chéo currency. |
| `status` | `str` | Không | Trạng thái giao dịch; canonical dùng enum, provider giữ chuỗi nguồn. Trạng thái run được mô tả riêng. |
| `txn_type` | `TxnType` | Không | Loại nghiệp vụ canonical; xem enum và mapping nguồn. |
| `transaction_time` | `datetime` | Không | Thời điểm giao dịch provider; ISO timezone-aware khi parse, chuẩn hóa UTC. |
| `source_file` | `str` | Không | Tên file sao kê nguồn; HTTP lấy basename của tên upload. |
| `source_row_number` | `int` | Không | Số bản ghi CSV tính cả header; data đầu tiên là 2. CSV nhiều dòng trong ô dùng thứ tự record, không phải dòng vật lý. |
| `raw` | `dict[str, Any]` | Không | Payload nguồn phục vụ lưu/truy vết; raw CSV được spill vào file tạm trong lúc matching, đọc tuần tự khi persist và không trả qua API query/export. |

## 5. ReconResult — evidence và cột export

| Trường | Kiểu Python | Nullable | Ý nghĩa / quy tắc |
|---|---|---|---|
| `run_id` | `str` | Không | ID run đối soát; UUID ở persistence/API, chuỗi trong domain result. |
| `result_type` | `ReconResultType` | Không | Một trong 9 loại kết quả, định nghĩa ở phần matching. |
| `region` | `Region` | Không | Khu vực nguồn; VN, ASIA hoặc INDIA. |
| `tenant_id` | `str  /  None` | Có | Tenant nguồn; bắt buộc với ASIA, cấu hình hiện có asia-id/asia-ph; VN/INDIA dùng null. |
| `provider` | `str` | Không | Mã provider chuẩn hóa trim/uppercase; ONEPAY, ESPAY, 2C2P, RAZORPAY theo cấu hình hiện tại. |
| `txn_type` | `TxnType  /  None` | Có | Loại nghiệp vụ canonical; xem enum và mapping nguồn. |
| `system_txn_id` | `str  /  None` | Có | ID giao dịch canonical phía system; null khi chỉ có phía provider. |
| `system_provider_txn_id` | `str  /  None` | Có | provider_txn_id của canonical, giữ bằng chứng để kiểm tra parent refund INDIA. |
| `system_status` | `CanonicalStatus  /  None` | Có | Trạng thái canonical tại lần chạy; dùng tính refund. |
| `system_amount_minor` | `int  /  None` | Có | Tiền system ở minor unit; null khi thiếu hoặc không có phía system. |
| `system_currency` | `str  /  None` | Có | Currency system; độc lập với provider_currency. |
| `refund_of` | `str  /  None` | Có | Tham chiếu parent của refund; ý nghĩa khóa khác nhau theo region. |
| `refunded_amount_minor` | `int  /  None` | Có | Tổng tiền đã refund ghi nhận trên parent, theo currency system; không cộng thêm vào tiền giao dịch. |
| `provider_txn_id` | `str  /  None` | Có | ID giao dịch provider; khóa exact ưu tiên 1. Refund ASIA loại bỏ ID kế thừa parent khi matching. |
| `provider_amount_minor` | `int  /  None` | Có | Tiền provider ở minor unit; null khi không có phía provider. |
| `provider_currency` | `str  /  None` | Có | Currency provider; độc lập với system_currency. |
| `source_file` | `str  /  None` | Có | Tên file sao kê nguồn; HTTP lấy basename của tên upload. |
| `source_row_number` | `int  /  None` | Có | Số bản ghi CSV tính cả header; data đầu tiên là 2. CSV nhiều dòng trong ô dùng thứ tự record, không phải dòng vật lý. |
| `delta_amount` | `int  /  None` | Có | Tiền system trừ provider chỉ khi ghép được hai phía cùng currency và có đủ tiền; còn lại null. |
| `reason` | `str  /  None` | Có | Lý do phân loại hoặc khóa matching; chuỗi diễn giải, không phải enum ổn định. |

## 6. Mapping Mongo → canonical

Cấu hình mặc định bootstrap:

| Scope | Database | Provider | Currency kỳ vọng | Timezone ngày nghiệp vụ | Trường lọc ngày |
|---|---|---|---|---|---|
| VN | paymenthub_vn | ONEPAY | VND giả định | Asia/Ho_Chi_Minh | CreatedOn |
| ASIA / asia-id | paymenthub_asia_id | ESPAY | IDR | Asia/Jakarta | CreatedAt |
| ASIA / asia-ph | paymenthub_asia_ph | 2C2P | PHP | Asia/Manila | CreatedAt |
| INDIA | paymenthub_india | RAZORPAY | INR | Asia/Kolkata | createdAt |

Collection mặc định `payment_transactions`. Source đọc `[00:00 ngày nghiệp vụ, 00:00 ngày kế tiếp)` theo timezone rồi chuyển biên sang UTC.

| Canonical | VN | ASIA | INDIA |
|---|---|---|---|
| txn_id | _id | _id | _id |
| provider_txn_id | PaymentTransactionId | ExternalTxnId, bỏ khi REFUND hoặc có RefundFor | transactionId |
| provider_ref_id | null | PaymentRefId | null |
| merchant_ref | _id | _id | referenceId chỉ LOAD/REFUND |
| amount_minor | Amount → VND minor | AmountAsDecimal → minor; đối chiếu Amount nếu có | amount → INR minor |
| currency | cấu hình VND | Currency | currency |
| refund_of | OrderInfo khi REFUND | RefundFor | relatedPaymentId |
| refunded_amount_minor | null | null | amountRefunded → minor |
| order_ids | BillIds | BillIds | sourceIds |
| merchant_id | MerchantId | MerchantCode | merchantId |
| provider_merchant_id | null | MerchantRefCode | null |
| service_id | ServiceType | ServiceId | serviceType |
| customer_id | CustomerId | CustomerId | customerId |
| channel_raw | PaymentMethod | Channel | instrumentType |
| updated_at | UpdatedOn | UpdatedAt | updatedAt |
| paid_at | PaidDate | PaidDate | paidAt |
| provider_created_at | null | null | gatewayCreatedAt |
| failure_reason | Message | null | failureReason |
| provider_poll_count | PollCount | null | reconcileAttempts |

VN phân loại REFUND bằng PaymentMethod cấu hình, CARD_LINK bằng danh sách method; còn lại PAYMENT. ASIA Type: 1 PAYMENT, 2 REFUND, 3 CARD_LINK, 4 TOKENIZATION; khác là UNKNOWN. INDIA chuẩn hóa chuỗi type sang enum.

ASIA thiếu Currency → NO_CURRENCY; khác currency tenant hoặc Amount khác AmountAsDecimal → INVALID. CARD_LINK/TOKENIZATION → NOT_APPLICABLE. INDIA thiếu currency → NO_CURRENCY; sai INR hoặc parse tiền thất bại → INVALID. VN parse Amount hợp lệ → ASSUMED_CURRENCY.

VN/ASIA callback_attempts = 1 nếu IsSentCallback là true, còn lại 0; không phải tổng retry. INDIA ưu tiên độ dài merchant_callback_log, nếu thiếu dùng callback_error.attempts, mặc định 0. ASIA chỉ nhận isSyntheticTestData theo mapping đã cấu hình (`synthetic` → true); giá trị chưa xác minh gây lỗi. VN/INDIA hiện không nhận diện test flag (false).

Mapping trạng thái hiện tại:

### VN

| Giá trị nguồn (INDIA: type, status) | CanonicalStatus |
|---|---|
| `0` | `PENDING` |
| `1` | `SUCCESS` |
| `2` | `FAILED` |
| `3` | `SUCCESS` |
| `4` | `REFUNDED` |
| `5` | `UNKNOWN` |
| `6` | `CANCELLED` |

Không có mapping tương ứng → UNKNOWN.

### ASIA

| Giá trị nguồn (INDIA: type, status) | CanonicalStatus |
|---|---|
| `0` | `CREATED` |
| `1` | `PENDING` |
| `2` | `SUCCESS` |
| `3` | `FAILED` |
| `4` | `REFUNDED` |
| `5` | `PARTIAL_REFUNDED` |

Không có mapping tương ứng → UNKNOWN.

### INDIA

| Giá trị nguồn (INDIA: type, status) | CanonicalStatus |
|---|---|
| `(TxnType.LOAD, 'created')` | `CREATED` |
| `(TxnType.LOAD, 'success')` | `SUCCESS` |
| `(TxnType.LOAD, 'failed')` | `FAILED` |
| `(TxnType.PAYMENT, 'pending')` | `PENDING` |
| `(TxnType.PAYMENT, 'authorized')` | `PENDING` |
| `(TxnType.PAYMENT, 'captured')` | `SUCCESS` |
| `(TxnType.PAYMENT, 'cancelled')` | `CANCELLED` |
| `(TxnType.PAYMENT, 'failed')` | `FAILED` |
| `(TxnType.PAYMENT, 'refunded')` | `REFUNDED` |
| `(TxnType.REFUND, 'created')` | `CREATED` |
| `(TxnType.REFUND, 'pending')` | `PENDING` |
| `(TxnType.REFUND, 'processed')` | `SUCCESS` |
| `(TxnType.REFUND, 'success')` | `SUCCESS` |
| `(TxnType.REFUND, 'failed')` | `FAILED` |
| `(TxnType.REFUND, 'reversed')` | `FAILED` |
| `(TxnType.REFUND, 'reversal_failed')` | `FAILED` |

Không có mapping tương ứng → UNKNOWN.

## 7. Parser và chất lượng dữ liệu đầu vào

[CsvFixtureParser](../src/reconciliation/adapters/csv_fixture.py) nhận CSV nội bộ UTF-8 (có thể có BOM), phân cách dấu phẩy. Các header bắt buộc: `provider`, `region`, `tenant_id`, `provider_txn_id`, `provider_ref_id`, `merchant_ref`, `merchant_id`, `amount_minor`, `currency`, `status`, `txn_type`, `transaction_time`.

Header bắt buộc không có nghĩa mọi ô đều bắt buộc: các ID tùy chọn được đổi chuỗi rỗng thành null; tenant bắt buộc với ASIA. `status` provider phải có giá trị nhưng chưa kiểm tra theo enum trạng thái provider. `transaction_time` phải có timezone. RunMatching kiểm tra tất cả dòng cùng region/tenant/provider của request; dòng ngoài kỳ vẫn được lưu nhưng không tham gia matching; chỉ dòng tại hoặc sau cutoff có `late_arriving=true`.

| ValidationCode | Điều kiện |
|---|---|
| MISSING_COLUMN | Thiếu header hoặc ô bắt buộc rỗng |
| INVALID_TYPE | Sai số nguyên, region/type, số cột, tenant hoặc ràng buộc model |
| INVALID_DATE | Ngày không phải ISO timezone-aware |
| UNKNOWN_CURRENCY | Currency ngoài danh sách Money |
| NEGATIVE_AMOUNT | amount_minor < 0 |
| EMPTY_ROW | Record rỗng |
| DUPLICATE_ROW | Toàn bộ giá trị record trùng sau trim; khác duplicate ID trong engine |
| INVALID_ENCODING | Không decode được UTF-8 |
| INVALID_DELIMITER | Không nhận diện được delimiter hoặc delimiter không phải dấu phẩy |

`ValidationIssue`: `row: int|null` (null cho lỗi file), `field: str|null`, `code: ValidationCode`, `message: str`. `ParseReport`: `rows` là tuple dòng hợp lệ, `issues` là tuple lỗi, `valid = không có issues`. Chỉ một lỗi cũng khiến use case từ chối toàn bộ file, không chạy matching phần hợp lệ. HTTP hiện trả chuỗi chi tiết lỗi, chưa trả trực tiếp cấu trúc ParseReport.

## 8. Quy tắc matching và 9 kết quả

Thứ tự: dedupe → loại system không reconcilable/tiền invalid → exact provider_txn_id → exact provider_ref_id (ASIA) → exact merchant_ref → fuzzy → chẩn đoán exact ID khác currency → missing.

System duplicate theo global key; provider duplicate theo scope và ID đầu tiên có giá trị trong provider_txn_id, provider_ref_id (ASIA), merchant_ref. Giữ dòng đầu, mỗi dòng dư có một kết quả DUPLICATE. Không có ID thì không dedupe bằng tiền/thời gian.

Fuzzy yêu cầu cùng scope có currency, cùng merchant_id khác null, cùng amount_minor và chênh lệch thời gian tuyệt đối ≤ 300 giây. Chọn ứng viên gần thời gian nhất; hòa thì theo thứ tự input. Exact hiện chọn ứng viên đầu tiên và chưa bắt buộc cùng txn_type hoặc status.

| Kết quả | Định nghĩa triển khai |
|---|---|
| MATCHED | Ghép exact, cùng currency và amount_minor |
| AMOUNT_MISMATCH | Ghép được, cùng currency nhưng khác amount_minor |
| CURRENCY_MISMATCH | Exact ID trong cùng region/tenant/provider nhưng khác currency, sau các bước matching đủ scope; delta_amount null |
| FUZZY_MATCHED | Ghép bằng merchant + tiền + thời gian theo quy tắc trên |
| MISSING_IN_SYSTEM | Provider còn lại không có counterpart đủ điều kiện |
| MISSING_IN_PROVIDER | System còn lại không có counterpart đủ điều kiện |
| DUPLICATE | Dòng dư sau dedupe từng phía |
| NOT_RECONCILABLE | System is_reconcilable false hoặc loại CARD_LINK/TOKENIZATION |
| INVALID_DATA | System còn reconcilable nhưng amount_status INVALID hoặc NO_CURRENCY |

Không có STATUS_MISMATCH trong model hiện tại. MATCHED không chứng minh trạng thái hai phía giống nhau. NOT_RECONCILABLE được xét trước INVALID_DATA.

## 9. Báo cáo tài chính và công thức metric

Nguồn: [summary.py](../src/reconciliation/application/summary.py). Aggregate được tính
một lần từ `ReconResult` khi hoàn tất run và lưu trong `job_run_log.metrics`;
endpoint summary đọc snapshot này, không đọc lại Mongo hoặc toàn bộ `recon_result`.
Run cũ chưa có snapshot vẫn được tái tạo từ persisted results để tương thích.

Quy ước: `A` = toàn bộ dòng kết quả; `F` = A bỏ DUPLICATE và NOT_RECONCILABLE. Dấu hiệu có phía system là system_txn_id khác null; có phía provider là source_row_number khác null. Các tổng tài chính bên dưới dùng F, không mặc định lọc giao dịch SUCCESS.

| Trường/chỉ số | Kiểu | Định nghĩa |
|---|---|---|
| system_rows / system_count ở summary run | int | Số input system sau loại deleted/test, trước dedupe; lưu trong job metrics |
| provider_rows / provider_count ở summary run | int | Số record provider hợp lệ trước dedupe |
| summary | map[str,int] | Đếm số ReconResult theo result_type trên A; loại không xuất hiện có thể vắng khóa |
| totals_by_currency | list[CurrencyTotals] | Tổng riêng mỗi currency trên F |
| CurrencyTotals.currency | str | Currency nhóm |
| CurrencyTotals.system_amount_minor | int hoặc null | Tổng amount system có currency này; bỏ tiền phía system của INVALID_DATA; null nếu không có giá trị đóng góp |
| CurrencyTotals.provider_amount_minor | int hoặc null | Tổng amount provider có currency này; null nếu không có giá trị đóng góp |
| CurrencyTotals.system_amount_count / provider_amount_count | int | Số giá trị tiền thực sự đóng góp ở từng phía |
| CurrencyTotals.system_missing_amount_count / provider_missing_amount_count | int | Phía đã biết currency nhưng thiếu tiền; system INVALID_DATA cũng được tính thiếu |
| CurrencyTotals.delta_amount_minor | int hoặc null | Tổng system − tổng provider. Phía vắng góp tổng rỗng 0; null nếu nhóm thiếu tiền hoặc toàn tập đang tổng hợp có unallocated_currency_count > 0 |
| unallocated_currency_count | int | Số phía giao dịch có evidence nhưng thiếu currency; có thể đếm hai phía của cùng một dòng |
| breakdowns | list[SummaryBreakdown] | Nhóm theo region, tenant_id, provider, txn_type, result |
| SummaryBreakdown.region / tenant_id / provider / txn_type / result | str, tenant/type có thể null | Các chiều của nhóm; result chính là result_type |
| SummaryBreakdown.system_count / provider_count | int | Số dòng có ID system / source_row_number trên toàn nhóm kể cả DUPLICATE, NOT_RECONCILABLE |
| SummaryBreakdown.totals_by_currency / unallocated_currency_count | list / int | Công thức như trên nhưng phần tài chính bỏ DUPLICATE, NOT_RECONCILABLE trong nhóm |

`sum(summary.values())` không phải system_rows + provider_rows vì một cặp ghép chỉ tạo một kết quả. Tổng tiền trên F vẫn bao gồm REFUND; không phải doanh thu ròng (không tự trừ refund). Không cộng parent.refunded_amount_minor vào tổng này.

`financial_summary` gồm `totals_by_currency`, `unallocated_currency_count`, `refund_parent_metrics`, `refund_cross_checks`, `breakdowns`, `refunds_by_currency`; các collection là tuple ở application và array trong JSON.

### RefundParentMetrics — tỷ lệ giao dịch parent đã refund

Parent hợp lệ để đếm: dòng F có system_txn_id và txn_type PAYMENT hoặc LOAD.

| Trường | Công thức / ý nghĩa |
|---|---|
| eligible_parent_count | Số parent nói trên |
| refunded_parent_count | Parent có system_status REFUNDED/PARTIAL_REFUNDED hoặc refunded_amount_minor > 0 |
| missing_evidence_count | Parent có status null/UNKNOWN và không có bằng chứng refunded nói trên |
| refunded_transaction_rate | refunded_parent_count / eligible_parent_count; chuỗi decimal 6 chữ số sau dấu chấm, không nhân 100; null nếu mẫu 0 hoặc missing_evidence_count > 0 |

Ví dụ: 2 parent, 1 đã refund, đủ evidence → `"0.500000"`. Một parent UNKNOWN chưa có evidence → tỷ lệ null, không tự coi là chưa refund.

### refunds_by_currency — tiền refund phía system

Chọn dòng F có system_txn_id, txn_type REFUND, status SUCCESS/REFUNDED/PARTIAL_REFUNDED; nhóm theo system_currency (có thể null).

| Trường RefundCurrencyTotals | Định nghĩa |
|---|---|
| currency | Currency nhóm, có thể null |
| count | Số refund chọn vào nhóm, kể cả thiếu/invalid tiền |
| amount_minor | Tổng tiền có giá trị và không INVALID_DATA; null nếu không có dòng hợp lệ hoặc thiếu currency |
| missing_amount_count | count trừ số dòng có tiền và không INVALID_DATA |

### refund_cross_checks — đối chiếu bằng chứng parent/child

Chỉ VN và INDIA. Khóa liên kết gồm region/tenant/provider/refund_of: VN đối chiếu parent.system_txn_id, INDIA đối chiếu parent.system_provider_txn_id. Không giả định parent ngoài snapshot là mất trong toàn hệ thống.

| Trường RefundCrossCheck | Ý nghĩa |
|---|---|
| region / tenant_id / provider / refund_of / currency | Scope và tham chiếu parent/currency của nhóm child |
| successful_child_count | Số child có status SUCCESS/REFUNDED/PARTIAL_REFUNDED |
| successful_child_amount_minor | Tổng tiền child thành công; null nếu không có child, thiếu currency hoặc evidence child không đủ |
| parent_refunded_amount_minor | Tổng refund lấy từ parent, có thể null |
| status | Kết quả kiểm tra dưới đây |

Thứ tự ưu tiên trạng thái: MISSING_PARENT_REFERENCE (không có tham chiếu), AMBIGUOUS_PARENT (nhiều parent), PARENT_NOT_IN_SNAPSHOT, MISSING_CURRENCY, CURRENCY_MISMATCH, CHILDREN_NOT_IN_SNAPSHOT, INCOMPLETE_CHILD_DATA, LINKED (VN đủ liên kết nhưng không so tổng parent), PARENT_TOTAL_UNAVAILABLE (INDIA), MATCHED hoặc AMOUNT_MISMATCH (so tổng INDIA).

Child được xem thiếu evidence nếu status null/UNKNOWN, hoặc là refund thành công nhưng tiền null/INVALID_DATA. Các trạng thái thất bại đã biết không đóng góp tổng refund thành công.

### Ví dụ tiền và null

- Một cặp system 100000 VND / provider 99000 VND → AMOUNT_MISMATCH, delta_amount = 1000.
- Một dòng system 50000 VND thiếu provider → delta_amount dòng là null; tổng nhóm đầy đủ evidence có delta_amount_minor = 50000.
- Một cặp khác currency → không trừ tiền hai currency; hai phía đi vào nhóm currency riêng.
- Nhóm không có dữ liệu → list rỗng; không tự dựng mọi currency bằng 0.
- Chưa có metric nghiệp vụ match_rate/missing_rate với mẫu số được thống nhất trong FinancialSummary; không gọi refunded_transaction_rate là tỷ lệ tiền refund.

## 10. API, snapshot, export và audit

| Trường | Hợp đồng |
|---|---|
| run_id / job_run_id | UUID run và job; POST trả hai ID |
| meta.data_as_of | POST/list: thời điểm tạo response; summary: finished_at hoặc started_at; results: finished_at. Không phải watermark đồng bộ nguồn |
| items | Danh sách run/result sau lọc và phân trang |
| next_cursor | Chuỗi base64 JSON gắn với scope filter/sort, null khi hết; không phải token ký bảo mật |
| result_key | API/DB: số thứ tự `integer` dương từ 1 trong snapshot trước lọc |
| limit | 1–200, mặc định 50 |
| sort | runs chỉ run_id; results chỉ result_key |
| replayed | Cờ nội bộ MatchingRunOutput khi trả lại run đã hoàn tất; POST response hiện không có trường này |

Endpoint results lọc `result_type`, áp dụng cursor và lấy `limit + 1` tại
PostgreSQL. Dòng dư xác định `next_cursor` rồi bị bỏ khỏi response. Summary và
report tiếp tục dùng toàn bộ snapshot đã commit.

Summary run chưa hoàn tất chỉ có metadata, các số tổng/financial_summary null. Results/report yêu cầu snapshot hoàn tất. Lỗi 404 không có run, 409 chưa hoàn tất/đang chạy cùng input, 422 input/cursor không hợp lệ, 503 query database không sẵn sàng.

Export dùng đúng các trường ReconResult ở mục 5, không có raw hoặc customer_id. CSV ghi số nguyên dạng văn bản và bảo vệ chuỗi có thể bị diễn giải là công thức. XLSX lưu các trường tiền dạng text. Parquet: tiền decimal128(38,0), source_row_number int64, trường khác string; schema export hiện cho phép nullable tất cả cột, khác annotation bắt buộc của domain. Metadata Parquet có data_dictionary và data_as_of. Report HTTP có X-Data-As-Of = finished_at.

Audit system bằng `(region, tenant_id, system_txn_id)`; audit provider bằng `(run_id, source_file, source_row_number)`. Kết quả lưu evidence tiền/status/refund để replay. Không lưu toàn bộ canonical source snapshot trong recon_result.

## 11. Prometheus — metric vận hành

Nguồn: [metrics.py](../src/reconciliation/adapters/metrics.py). Label chung: region, tenant_id (`none` nếu thiếu), provider uppercase. Các counter là bộ đếm process, có thể reset khi restart; không thay cho số liệu lịch sử trong DB.

| Tên metric | Loại / đơn vị | Label thêm | Khi cập nhật / ý nghĩa |
|---|---|---|---|
| paymenthub_reconciliation_requests_total | Counter / request | outcome | Mỗi execute kết thúc, gồm completed, replayed, invalid_statement, in_progress, failed |
| paymenthub_reconciliation_duration_seconds | Histogram / giây | outcome | Thời gian execute, bao gồm replay và thất bại; không phải toàn bộ HTTP upload/network |
| paymenthub_reconciliation_input_rows_total | Counter / dòng | side=system/provider | Chỉ completed mới; cộng input rows trước dedupe |
| paymenthub_reconciliation_results_total | Counter / kết quả | result | Chỉ completed mới; cộng summary từng result |
| paymenthub_reconciliation_last_success_timestamp_seconds | Gauge / Unix giây | Không | Thời gian ghi nhận completed mới gần nhất; replay không đổi |
| paymenthub_reconciliation_dependency_duration_seconds | Histogram / giây | dependency, operation, outcome | system_source/get_transactions, repository/complete_run hoặc fail_run; outcome success/failed |

Histogram có các series _bucket, _sum, _count. Duration bucket: 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300 giây; dependency: 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30; cộng bucket +Inf.

p95 là percentile 95 ước lượng từ histogram theo cửa sổ thời gian, không phải trung bình. Có thể tính p95 execute toàn hệ thống bằng `histogram_quantile(0.95, sum by (le) (rate(paymenthub_reconciliation_duration_seconds_bucket[5m])))`. Requests/s dùng `sum(rate(paymenthub_reconciliation_requests_total[5m]))`; đây là request đối soát, không phải msg/s ingest.

Các chỉ số benchmark tuần 2 `msg/s`, `p95_ms`, `lost`, `duplicated` của ingest chưa được instrumentation này triển khai; không lấy counter reconciliation để thay thế.

## 12. PostgreSQL — danh mục đầy đủ cột vật lý

Schema chỉ dùng kiểu dựng sẵn của PostgreSQL. Mã có miền đóng dùng `varchar(n)` kèm `CHECK`; ID bên ngoài và nội dung tự do dùng `text`. PostgreSQL lưu `text` và `varchar(n)` theo cùng cơ chế, nên `varchar(n)` dùng để giới hạn dữ liệu chứ không nhằm tiết kiệm dung lượng. Các trường tiền dùng minor unit. `created_at` ở bảng persistence là thời điểm tạo bản ghi DB (default now()), khác created_at giao dịch canonical.

### ingest_message

| Cột | SQL type | Nullable / default / ràng buộc tại cột | Ý nghĩa |
|---|---|---|---|
| `event_id` | `uuid` | Không; `PRIMARY KEY` | ID bản tin ingest; khóa chống trùng tại ingest_message. |
| `event_type` | `varchar(64)` | Không; `NOT NULL` | Loại bản tin; schema chưa có CHECK danh sách loại. |
| `payload_hash` | `text` | Không; `NOT NULL` | Hash payload phục vụ nhận diện nội dung; migration chưa quy định thuật toán. |
| `accepted_at` | `timestamptz` | Không; `NOT NULL DEFAULT now()` | Thời điểm DB nhận bản tin, default now(). |

### canonical_event

| Cột | SQL type | Nullable / default / ràng buộc tại cột | Ý nghĩa |
|---|---|---|---|
| `event_day` | `date` | Không; `NOT NULL` | Ngày phân vùng canonical_event; quy tắc suy ra ngày chưa có writer triển khai trong pipeline hiện tại. |
| `event_id` | `uuid` | Không; `NOT NULL REFERENCES ingest_message(event_id)` | ID bản tin ingest; khóa chống trùng tại ingest_message. |
| `region` | `varchar(5)` | Không; `NOT NULL; CHECK` | Khu vực nguồn; VN, ASIA hoặc INDIA. |
| `tenant_id` | `text` | Có; `không default` | Tenant nguồn; bắt buộc với ASIA, cấu hình hiện có asia-id/asia-ph; VN/INDIA dùng null. |
| `txn_id` | `text` | Không; `NOT NULL` | ID giao dịch nguồn, chuyển thành chuỗi; duy nhất trong (region, tenant_id). |
| `txn_type` | `varchar(12)` | Không; `NOT NULL; CHECK` | Loại nghiệp vụ canonical; xem enum và mapping nguồn. |
| `provider` | `varchar(64)` | Không; `NOT NULL` | Mã provider chuẩn hóa trim/uppercase; ONEPAY, ESPAY, 2C2P, RAZORPAY theo cấu hình hiện tại. |
| `status` | `varchar(16)` | Không; `NOT NULL; CHECK` | Trạng thái giao dịch canonical. |
| `provider_txn_id` | `text` | Có; `không default` | ID giao dịch provider; khóa exact ưu tiên 1. Refund ASIA loại bỏ ID kế thừa parent khi matching. |
| `provider_ref_id` | `text` | Có; `không default` | Tham chiếu provider; exact ưu tiên 2 chỉ áp dụng ASIA. |
| `merchant_ref` | `text` | Có; `không default` | Tham chiếu merchant; exact ưu tiên 3. |
| `refund_of` | `text` | Có; `không default` | Tham chiếu parent của refund; ý nghĩa khóa khác nhau theo region. |
| `amount_minor` | `numeric(38, 0)` | Có; `không default` | Số nguyên đơn vị tiền nhỏ nhất; phải đọc cùng currency. Provider không nhận số âm. |
| `currency` | `varchar(3)` | Có; `CHECK nếu có` | Mã tiền trong danh sách Money hỗ trợ ở bảng exponent. |
| `updated_at` | `timestamptz` | Không; `NOT NULL` | Thời điểm cập nhật nguồn; bắt buộc tại schema này. |
| `is_late` | `boolean` | Không; `NOT NULL DEFAULT false` | Cờ event đến muộn; default false, chưa có rule cập nhật trong pipeline hiện tại. |

### job_run_log

| Cột | SQL type | Nullable / default / ràng buộc tại cột | Ý nghĩa |
|---|---|---|---|
| `job_run_id` | `uuid` | Không; `PRIMARY KEY` | UUID job thực thi; recon_run tham chiếu job_run_log. |
| `job_name` | `varchar(64)` | Không; `NOT NULL` | Tên job; repository ghi reconciliation. |
| `business_date` | `date` | Không; `NOT NULL` | Ngày kết thúc kỳ nghiệp vụ tạm thời; nguồn Mongo lọc theo khoảng UTC được resolve từ timezone/cutoff. |
| `region` | `varchar(5)` | Có; `CHECK nếu có` | Khu vực nguồn; VN, ASIA hoặc INDIA. |
| `tenant_id` | `text` | Có; `không default` | Tenant nguồn; bắt buộc với ASIA, cấu hình hiện có asia-id/asia-ph; VN/INDIA dùng null. |
| `provider` | `varchar(64)` | Có; `không default` | Mã provider chuẩn hóa trim/uppercase; ONEPAY, ESPAY, 2C2P, RAZORPAY theo cấu hình hiện tại. |
| `status` | `varchar(9)` | Không; `NOT NULL DEFAULT 'RUNNING'; CHECK` | PENDING/RUNNING/COMPLETED/FAILED; job được tạo ở RUNNING rồi chuyển COMPLETED hoặc FAILED. |
| `started_at` | `timestamptz` | Không; `NOT NULL DEFAULT now()` | Thời điểm bắt đầu attempt, default now(). |
| `finished_at` | `timestamptz` | Có; `không default` | Thời điểm kết thúc; null khi đang chạy, phải >= started_at nếu có. |
| `error_code` | `varchar(64)` | Có; `không default` | Mã lỗi job: INVALID_STATEMENT khi exception là ValueError, còn lại PROCESSING_FAILED. |
| `error_message` | `text` | Có; `không default` | INVALID_STATEMENT lưu str(error) tối đa 1000 ký tự; PROCESSING_FAILED chỉ lưu tên lớp exception. |
| `metrics` | `jsonb` | Không; `NOT NULL DEFAULT '{}'::jsonb` | JSONB khi hoàn tất: system_rows, provider_rows, results (số kết quả), summary; default {}. |

### recon_run

| Cột | SQL type | Nullable / default / ràng buộc tại cột | Ý nghĩa |
|---|---|---|---|
| `run_id` | `uuid` | Không; `PRIMARY KEY` | ID run đối soát; UUID ở persistence/API, chuỗi trong domain result. |
| `job_run_id` | `uuid` | Không; `NOT NULL REFERENCES job_run_log(job_run_id)` | UUID job thực thi; recon_run tham chiếu job_run_log. |
| `business_date` | `date` | Không; `NOT NULL` | Ngày kết thúc kỳ nghiệp vụ tạm thời; nguồn Mongo lọc theo khoảng UTC được resolve từ timezone/cutoff. |
| `region` | `varchar(5)` | Không; `NOT NULL; CHECK` | Khu vực nguồn; VN, ASIA hoặc INDIA. |
| `tenant_id` | `text` | Có; `không default` | Tenant nguồn; bắt buộc với ASIA, cấu hình hiện có asia-id/asia-ph; VN/INDIA dùng null. |
| `provider` | `varchar(64)` | Không; `NOT NULL` | Mã provider chuẩn hóa trim/uppercase; ONEPAY, ESPAY, 2C2P, RAZORPAY theo cấu hình hiện tại. |
| `source_file` | `text` | Không; `NOT NULL` | Tên file sao kê nguồn; HTTP lấy basename của tên upload. |
| `file_checksum` | `varchar(64)` | Không; `NOT NULL; CHECK` | SHA-256 dạng 64 ký tự hex viết thường của byte sao kê nguyên bản. |
| `business_timezone` | `varchar(64)` | Không; `NOT NULL` | Tên timezone IANA được lưu cùng run. |
| `cutoff_time` | `time` | Không; `NOT NULL` | Cutoff nhận từ input và được snapshot cùng run. Kỳ D tạm dùng `[(D-1) cutoff, D cutoff)`. |
| `allowed_lateness_seconds` | `integer` | Không; `NOT NULL DEFAULT 0` | Số giây cho phép muộn, >= 0, default 0; chưa áp dụng chính sách trong pipeline. |
| `status` | `varchar(9)` | Không; `NOT NULL DEFAULT 'PENDING'; CHECK` | PENDING/RUNNING/COMPLETED/FAILED; repository tạo run với RUNNING rồi chuyển COMPLETED hoặc FAILED. |
| `started_at` | `timestamptz` | Không; `NOT NULL DEFAULT now()` | Thời điểm bắt đầu attempt, default now(). |
| `finished_at` | `timestamptz` | Có; `không default` | Thời điểm kết thúc; null khi đang chạy, phải >= started_at nếu có. |
| `created_at` | `timestamptz` | Không; `NOT NULL DEFAULT now()` | Thời điểm tạo bản ghi DB, default now(). |

### recon_result

| Cột | SQL type | Nullable / default / ràng buộc tại cột | Ý nghĩa |
|---|---|---|---|
| `result_id` | `bigint` | Không; `GENERATED ALWAYS AS IDENTITY`; thuộc PK với business_date | Identity bigint nội bộ cho dòng kết quả. |
| `business_date` | `date` | Không; `NOT NULL`; partition key | Snapshot ngày nghiệp vụ từ recon_run; dùng partition pruning, không suy ra từ created_at. |
| `run_id` | `uuid` | Không; `NOT NULL` | ID run đối soát; UUID ở persistence/API, chuỗi trong domain result. |
| `job_run_id` | `uuid` | Không; `NOT NULL` | UUID job thực thi; recon_run tham chiếu job_run_log. |
| `result_key` | `integer` | Không; `NOT NULL; CHECK > 0` | Số thứ tự lưu trong run từ 1; unique với run_id và sắp xếp trực tiếp theo số. |
| `result_type` | `varchar(19)` | Không; `NOT NULL; CHECK` | Một trong 9 loại kết quả, định nghĩa ở phần matching. |
| `region` | `varchar(5)` | Không; `NOT NULL; CHECK` | Khu vực nguồn; VN, ASIA hoặc INDIA. |
| `tenant_id` | `text` | Có; `không default` | Tenant nguồn; bắt buộc với ASIA, cấu hình hiện có asia-id/asia-ph; VN/INDIA dùng null. |
| `provider` | `varchar(64)` | Không; `NOT NULL` | Mã provider chuẩn hóa trim/uppercase; ONEPAY, ESPAY, 2C2P, RAZORPAY theo cấu hình hiện tại. |
| `txn_type` | `varchar(12)` | Có; `CHECK nếu có` | Loại nghiệp vụ canonical; xem enum và mapping nguồn. |
| `system_txn_id` | `text` | Có; `không default` | ID giao dịch canonical phía system; null khi chỉ có phía provider. |
| `system_amount_minor` | `numeric(38, 0)` | Có; `không default` | Tiền system ở minor unit; null khi thiếu hoặc không có phía system. |
| `system_currency` | `varchar(3)` | Có; `CHECK nếu có` | Currency system; độc lập với provider_currency. |
| `provider_txn_id` | `text` | Có; `không default` | ID giao dịch provider; khóa exact ưu tiên 1. Refund ASIA loại bỏ ID kế thừa parent khi matching. |
| `provider_amount_minor` | `numeric(38, 0)` | Có; `không default` | Tiền provider ở minor unit; null khi không có phía provider. |
| `provider_currency` | `varchar(3)` | Có; `CHECK nếu có` | Currency provider; độc lập với system_currency. |
| `delta_amount` | `numeric(38, 0)` | Có; `không default` | Tiền system trừ provider chỉ khi ghép được hai phía cùng currency và có đủ tiền; còn lại null. |
| `source_file` | `text` | Có; `không default` | Tên file sao kê nguồn; HTTP lấy basename của tên upload. |
| `source_row_number` | `integer` | Có; `không default` | Số bản ghi CSV tính cả header; data đầu tiên là 2. CSV nhiều dòng trong ô dùng thứ tự record, không phải dòng vật lý. |
| `reason` | `text` | Có; `không default` | Lý do phân loại hoặc khóa matching; chuỗi diễn giải, không phải enum ổn định. |
| `created_at` | `timestamptz` | Không; `NOT NULL DEFAULT now()` | Thời điểm tạo bản ghi DB, default now(). |
| `system_provider_txn_id` | `text` | Có; `không default` | provider_txn_id của canonical, giữ bằng chứng để kiểm tra parent refund INDIA. |
| `system_status` | `varchar(16)` | Có; `CHECK nếu có` | Trạng thái canonical tại lần chạy; dùng tính refund. |
| `refund_of` | `text` | Có; `không default` | Tham chiếu parent của refund; ý nghĩa khóa khác nhau theo region. |
| `refunded_amount_minor` | `numeric(38, 0)` | Có; `không default` | Tổng tiền đã refund ghi nhận trên parent, theo currency system; không cộng thêm vào tiền giao dịch. |

### provider_txn

| Cột | SQL type | Nullable / default / ràng buộc tại cột | Ý nghĩa |
|---|---|---|---|
| `provider_row_id` | `bigint` | Không; `GENERATED ALWAYS AS IDENTITY`; thuộc PK với business_date | Identity bigint nội bộ cho dòng provider. |
| `business_date` | `date` | Không; `NOT NULL`; partition key | Snapshot ngày nghiệp vụ từ recon_run; dùng partition pruning, không suy ra từ transaction_time. |
| `run_id` | `uuid` | Không; `NOT NULL` | ID run đối soát; UUID ở persistence/API, chuỗi trong domain result. |
| `job_run_id` | `uuid` | Không; `NOT NULL` | UUID job thực thi; recon_run tham chiếu job_run_log. |
| `region` | `varchar(5)` | Không; `NOT NULL; CHECK` | Khu vực nguồn; VN, ASIA hoặc INDIA. |
| `tenant_id` | `text` | Có; `không default` | Tenant nguồn; bắt buộc với ASIA, cấu hình hiện có asia-id/asia-ph; VN/INDIA dùng null. |
| `provider` | `varchar(64)` | Không; `NOT NULL` | Mã provider chuẩn hóa trim/uppercase; ONEPAY, ESPAY, 2C2P, RAZORPAY theo cấu hình hiện tại. |
| `txn_type` | `varchar(12)` | Không; `NOT NULL; CHECK` | Loại nghiệp vụ canonical; xem enum và mapping nguồn. |
| `provider_txn_id` | `text` | Có; `không default` | ID giao dịch provider; khóa exact ưu tiên 1. Refund ASIA loại bỏ ID kế thừa parent khi matching. |
| `provider_ref_id` | `text` | Có; `không default` | Tham chiếu provider; exact ưu tiên 2 chỉ áp dụng ASIA. |
| `merchant_ref` | `text` | Có; `không default` | Tham chiếu merchant; exact ưu tiên 3. |
| `merchant_id` | `text` | Có; `không default` | ID merchant; dùng trong fuzzy matching, phải khác null. |
| `amount_minor` | `numeric(38, 0)` | Không; `NOT NULL` | Số nguyên đơn vị tiền nhỏ nhất; phải đọc cùng currency. Provider không nhận số âm. |
| `currency` | `varchar(3)` | Không; `NOT NULL; CHECK` | Mã tiền trong danh sách Money hỗ trợ ở bảng exponent. |
| `status` | `varchar(64)` | Không; `NOT NULL` | Trạng thái nguồn provider, không dùng enum canonical. |
| `transaction_time` | `timestamptz` | Không; `NOT NULL` | Thời điểm giao dịch provider; ISO timezone-aware khi parse, chuẩn hóa UTC. |
| `late_arriving` | `boolean` | Không; `NOT NULL DEFAULT false` | True khi transaction_time của provider row tại hoặc sau cutoff của run. Row ngoài kỳ vẫn được lưu để audit nhưng không tham gia matching. |
| `source_file` | `text` | Không; `NOT NULL` | Tên file sao kê nguồn; HTTP lấy basename của tên upload. |
| `source_row_number` | `integer` | Không; `NOT NULL` | Số bản ghi CSV tính cả header; data đầu tiên là 2. CSV nhiều dòng trong ô dùng thứ tự record, không phải dòng vật lý. |
| `raw` | `jsonb` | Không; `NOT NULL DEFAULT '{}'::jsonb` | Payload nguồn phục vụ lưu/truy vết; không trả qua API query hoặc export kết quả. |
| `ingested_at` | `timestamptz` | Không; `NOT NULL DEFAULT now()` | Thời điểm ghi dòng provider vào DB, default now(). |

### window_aggregate

| Cột | SQL type | Nullable / default / ràng buộc tại cột | Ý nghĩa |
|---|---|---|---|
| `window_start` | `timestamptz` | Không; thuộc `PRIMARY KEY` | Đầu cửa sổ tổng hợp năm phút. |
| `window_end` | `timestamptz` | Không; `CHECK > window_start` | Cuối cửa sổ tổng hợp. |
| `region` | `varchar(5)` | Không; thuộc `PRIMARY KEY`; `CHECK` | VN, ASIA hoặc INDIA. |
| `tenant_id` | `text` | Không; `DEFAULT ''`; thuộc `PRIMARY KEY` | Tenant; chuỗi rỗng biểu diễn scope không có tenant. |
| `provider` | `varchar(64)` | Không; thuộc `PRIMARY KEY` | Provider của nhóm tổng hợp. |
| `currency` | `varchar(3)` | Không; `DEFAULT ''`; thuộc `PRIMARY KEY`; `CHECK` | Currency hỗ trợ hoặc chuỗi rỗng khi nhóm không có currency. |
| `total_count` | `integer` | Không; `CHECK >= 0` | Tổng giao dịch trong cửa sổ. |
| `success_count` | `integer` | Không; `CHECK >= 0` | Số giao dịch thành công. |
| `failed_count` | `integer` | Không; `CHECK >= 0` | Số giao dịch thất bại. |
| `pending_count` | `integer` | Không; `CHECK >= 0` | Số giao dịch đang chờ. |
| `success_rate` | `numeric(7,6)` | Có; `CHECK 0..1` | Tỷ lệ thành công, null khi chưa xác định được mẫu số. |
| `computed_at` | `timestamptz` | Không; `DEFAULT now()` | Thời điểm tạo hoặc cập nhật aggregate. |

### Khóa, index và vòng đời

- ingest_message: PK event_id. canonical_event: FK event_id → ingest_message; PK (event_day,event_id), partition RANGE(event_day). Migration chưa tạo partition con; schema này chưa được RunMatching ghi (pipeline hiện đọc Mongo trực tiếp).
- job_run_log: PK job_run_id; index (job_name,business_date,region,tenant_id,provider).
- recon_run: PK run_id; FK job_run_id → job_run_log; unique (run_id,job_run_id), (run_id,job_run_id,business_date) và (business_date,provider,file_checksum); index (business_date,region,tenant_id,provider). Khóa idempotency chưa chứa region/tenant; repository kiểm tra và từ chối nếu input trùng khóa nhưng scope khác.
- recon_result: partition ngày theo business_date; PK (business_date,result_id); FK (run_id,job_run_id,business_date) → recon_run, ON DELETE CASCADE; unique (business_date,run_id,result_key); CHECK giới hạn 9 result_type, result_key > 0 và source_row_number > 0 nếu có. Index (run_id,result_type,result_key); partial index (run_id,region,tenant_id,provider,result_type) cho kết quả ngoài MATCHED/FUZZY_MATCHED.
- provider_txn: partition ngày theo business_date; PK (business_date,provider_row_id); FK (run_id,job_run_id,business_date) → recon_run, ON DELETE CASCADE; unique (business_date,run_id,source_file,source_row_number); CHECK amount_minor >= 0 và source_row_number > 0. Index (run_id,source_file,source_row_number), scope/currency/transaction_time; partial index scope/currency/provider_txn_id khác null; BRIN transaction_time.
- window_aggregate: PK (window_start,region,tenant_id,provider,currency); CHECK window_end > window_start, các count không âm và success_rate trong khoảng 0..1.
- canonical_event: B-tree (region,tenant_id,provider,updated_at DESC) và BRIN updated_at.
- Run COMPLETED được replay từ dữ liệu đã lưu, không tạo thêm kết quả; concurrent cùng input bị chặn bằng advisory lock. Run lỗi có thể retry trên cùng ID. Provider rows, results và chuyển trạng thái COMPLETED được commit cùng transaction.

## 13. Phần chưa hoàn thiện và quy tắc cập nhật

- Cutoff/timezone cho provider rows đã có quy ước tạm. Allowed lateness, system late evidence và tự động đưa staged row vào run kế tiếp chưa triển khai.
- Chưa có pipeline ingest ghi canonical_event/partition con trong luồng đối soát này; benchmark ingest và rule cảnh báo vắng file cần triển khai riêng.
- Parser hiện là CSV fixture nội bộ; chưa khẳng định hỗ trợ định dạng sao kê production của từng provider.
- Mapping provider/currency/instrument và flag nguồn dựa vào cấu hình hiện có. Thêm tenant, currency hoặc trạng thái cần cập nhật mapping, Dictionary và kiểm thử tương ứng.
- Rate nghiệp vụ mới cần chốt tập input, điều kiện loại trừ, tử/mẫu, currency, cách xử lý mẫu 0/thiếu evidence trước khi đưa lên dashboard.
- Khi thay model/migration/summary/export, cập nhật cùng PR các mục tương ứng. SQL nullability và export nullability phải ghi riêng; không suy ra từ mỗi annotation Python.

## 14. Ví dụ kiểm chứng trong repository

- [Money](../tests/unit/test_money.py), [scope](../tests/unit/test_scope.py).
- [Adapter ASIA](../tests/unit/test_asia.py), [VN](../tests/unit/test_mongo_vn.py), [INDIA](../tests/unit/test_mongo_india.py).
- [CSV validation](../tests/unit/test_csv_fixture_parser.py), [matching](../tests/unit/test_matching_engine.py).
- [Financial summary](../tests/unit/test_reconciliation_summary.py), [query/export](../tests/unit/test_reconciliation_query_api.py), [metrics](../tests/unit/test_reconciliation_metrics.py).
- [Repository](../tests/unit/test_postgres_repository.py), [run lifecycle](../tests/integration/test_run_lifecycle.py).

Các link trên là nơi kiểm chứng định nghĩa; không thay thế kết quả chạy test của một phiên bản cụ thể.
