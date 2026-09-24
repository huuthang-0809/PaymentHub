# Dữ liệu MongoDB tổng hợp

Generator tạo các document nguồn có tính tất định cho adapter của từng khu vực.
MongoDB lưu dữ liệu theo cấu trúc của nguồn, không lưu canonical transaction.

Sản lượng mặc định:

| Database | Collection | Số document |
|---|---|---:|
| `paymenthub_vn` | `payment_transactions` | 5.000 |
| `paymenthub_asia_id` | `payment_transactions` | 2.500 |
| `paymenthub_asia_ph` | `payment_transactions` | 2.500 |
| `paymenthub_india` | `payment_transactions` | 5.000 |

Khởi động MongoDB và chạy generator một lần:

```bash
docker compose up -d --build mongodb generator
docker compose logs generator
```

Chạy lại generator với cùng seed:

```bash
docker compose run --rm generator
```

Các document dùng `_id` tất định và được ghi bằng bulk upsert, vì vậy chạy lại
cùng seed không làm tăng số lượng document. Có thể đổi quy mô hoặc seed mà không
cần sửa code:

```bash
GENERATOR_SEED=20260924 RECORDS_PER_REGION=32 \
  docker compose run --rm generator
```

Đáp án được ghi vào `tests/generated/ground_truth.json`. Các sao kê provider
được ghi vào `tests/generated/provider-statements/`, mỗi reconciliation scope
có một file CSV.
Toàn bộ file sinh ra có thể được tái tạo từ seed.

Với seed mặc định, các file gồm:

- `onepay_20260923.csv`
- `espay_asia-id_20260923.csv`
- `2c2p_asia-ph_20260923.csv`
- `razorpay_20260923.csv`

Kiểm tra số lượng document trong các collection:

```bash
docker compose exec mongodb mongosh --quiet --eval '
for (const name of [
  "paymenthub_vn",
  "paymenthub_asia_id",
  "paymenthub_asia_ph",
  "paymenthub_india"
]) {
  const count = db.getSiblingDB(name).payment_transactions.countDocuments({});
  print(name + ": " + count);
}'
```

Dataset chứa các mutation reconciliation tất định ở cả hai phía: cặp khớp,
lệch số tiền, lệch currency, thiếu dữ liệu ở một phía, provider bị trùng, fuzzy
match, giao dịch không cần reconciliation và dữ liệu system không hợp lệ. `_id`
trong MongoDB luôn duy nhất.

## Chiến lược sinh dữ liệu reconciliation hai phía

Fixture reconciliation được sinh từ seed cố định theo ba giai đoạn:

```text
seed -> cặp dữ liệu chuẩn -> mutation tất định -> đáp án chính xác
                                  |
                                  +-> document nguồn trong MongoDB
                                  +-> dòng sao kê provider
```

Sao kê provider không được tạo từ canonical transaction. Hai phía cùng bắt đầu
từ một đặc tả base case rõ ràng, nhưng raw Mongo document và provider row được
render độc lập. Nhờ đó, lỗi adapter không bị sao chép sang cả hai phía rồi bị che
khuất bởi một phép kiểm tra vòng tròn.

### Phạm vi bao phủ

Mỗi reconciliation scope phải chứa đủ chín giá trị `ReconResultType`:

| Khu vực | Tenant | Provider | Sao kê |
|---|---|---|---|
| `VN` | không có | `ONEPAY` | `onepay_<seed>.csv` |
| `ASIA` | `asia-id` | `ESPAY` | `espay_asia-id_<seed>.csv` |
| `ASIA` | `asia-ph` | `2C2P` | `2c2p_asia-ph_<seed>.csv` |
| `INDIA` | không có | `RAZORPAY` | `razorpay_<seed>.csv` |

Do số record Asia được chia cho hai tenant, `RECORDS_PER_REGION` phải có giá trị
ít nhất là 16 để mỗi scope nhận được một chu kỳ mutation đầy đủ gồm tám system
record. Giá trị mặc định đáp ứng điều kiện này.

Bộ mutation bắt buộc cho mỗi scope:

| Case | Mutation áp dụng lên cặp chuẩn | Kết quả chính xác |
|---|---|---|
| `exact-match` | Giữ nguyên cả hai phía | `MATCHED` |
| `amount-mismatch` | Chỉ thay amount phía provider | `AMOUNT_MISMATCH` |
| `currency-mismatch` | Chỉ thay currency phía provider và giữ exact identifier | `CURRENCY_MISMATCH` |
| `missing-in-provider` | Loại bỏ provider row | `MISSING_IN_PROVIDER` |
| `missing-in-system` | Loại bỏ Mongo document | `MISSING_IN_SYSTEM` |
| `duplicate` | Thêm provider row thứ hai có cùng reconciliation identifier | một `MATCHED` và một `DUPLICATE` |
| `fuzzy-match` | Thay mọi exact identifier nhưng giữ scope, merchant, amount và timestamp trong 300 giây | `FUZZY_MATCHED` |
| `not-reconcilable` | Đổi system transaction sang loại không reconciliation và bỏ provider row | `NOT_RECONCILABLE` |
| `invalid-data` | Làm hỏng amount hoặc bỏ currency phía system, đồng thời bỏ provider row | `INVALID_DATA` |

Hai dòng trong case duplicate phải khác nhau ở một field không thuộc identity,
chẳng hạn `transaction_time`. Fixture parser sẽ từ chối hai dòng CSV giống hệt
nhau trước khi chúng tới matching engine.

Mỗi case sử dụng identifier và merchant riêng trong scope. Vì merchant là duy
nhất, các timestamp gần nhau không thể gây fuzzy match ngoài ý muốn. ID có định
dạng ổn định chứa scope, seed và index; thứ tự lặp và thứ tự CSV cũng ổn định.

### Dữ liệu đặc thù theo khu vực

Ma trận mutation chung được áp dụng cho mọi scope bằng đúng cấu trúc nguồn của
khu vực đó. Các assertion bổ sung kiểm tra hành vi không thể biểu diễn bằng một
cặp dữ liệu chung.

Với Asia, mỗi tenant có một payment và refund tương ứng. Refund kế thừa
`ExternalTxnId` của payment, có `PaymentRefId` riêng và trỏ tới payment qua
`RefundFor`. Provider refund row giữ giá trị kế thừa trong `provider_txn_id` và
đặt reference của refund trong `provider_ref_id`. Kết quả mong đợi là `MATCHED`
theo `provider_ref_id`: adapter không được đưa `provider_txn_id` của refund vào
canonical transaction và matching engine không được ghép refund với payment
cha. Case này được sinh độc lập cho `asia-id` và `asia-ph`.

Với India, system document có `transactionId` và chủ ý không có `referenceId`.
Provider row dùng cùng `provider_txn_id`, còn `provider_ref_id` và `merchant_ref`
để trống. Kết quả mong đợi là `MATCHED` theo `transactionId`.

Với VN, case không reconciliation dùng `LINKCARD` và giữ hành vi adapter mặc
định currency là `VND`. Asia và India sử dụng các giá trị nguồn tương ứng để tạo
case không reconciliation và dữ liệu không hợp lệ.

### Lưu đáp án chính xác

Mỗi mutation đồng thời trả về Mongo document, provider row và expected result.
Expected result được khai báo bởi chính mutation, tuyệt đối không được lấy bằng
cách chạy `MatchingEngine` hoặc adapter khu vực.

Sau khi áp dụng tất cả mutation, provider row được sắp theo thứ tự tất định và
được gán số dòng CSV cuối cùng. Generator ghi oracle vào trường `reconciliation`
trong `ground_truth.json`, nhóm theo từng sao kê:

```json
{
  "reconciliation": {
    "onepay_20260923.csv": {
      "scope": {
        "region": "VN",
        "tenant_id": null,
        "provider": "ONEPAY"
      },
      "system_rows": 8,
      "provider_rows": 7,
      "expected_summary": {
        "MATCHED": 2,
        "AMOUNT_MISMATCH": 1,
        "CURRENCY_MISMATCH": 1,
        "MISSING_IN_SYSTEM": 1,
        "MISSING_IN_PROVIDER": 1,
        "DUPLICATE": 1,
        "FUZZY_MATCHED": 1,
        "NOT_RECONCILABLE": 1,
        "INVALID_DATA": 1
      },
      "expected_results": [
        {
          "case_id": "amount-mismatch",
          "result_type": "AMOUNT_MISMATCH",
          "system_txn_id": "vn-20260923-000001",
          "provider_txn_id": "onepay-20260923-000001",
          "source_row_number": 3,
          "system_amount_minor": 100001,
          "provider_amount_minor": 100000,
          "system_currency": "VND",
          "provider_currency": "VND",
          "delta_amount": 1
        }
      ]
    }
  }
}
```

Số lượng trong ví dụ tương ứng với một bộ mutation bắt buộc; filler và case đặc
thù theo khu vực sẽ bổ sung row và result. Số lượng thực tế được tính từ các case
đã sinh. Mỗi expected result lưu đủ thông tin để xác định hai phía và kiểm tra
phân loại: result type, transaction identifier, số dòng nguồn, amount, currency
và delta. Giá trị thiếu hoặc không áp dụng được lưu rõ ràng dưới dạng `null`.

### Kiểm chứng

Với mỗi sao kê, quá trình kiểm chứng parse provider CSV, chuyển Mongo document
qua adapter khu vực thật, chạy matching đúng scope rồi so sánh kết quả đã chuẩn
hóa với oracle:

```python
assert actual_system_rows == oracle["system_rows"]
assert actual_provider_rows == oracle["provider_rows"]
assert actual_summary == oracle["expected_summary"]
assert normalize(actual_results) == normalize(oracle["expected_results"])
```

Phép so sánh bao phủ mọi field của result, không chỉ số lượng tổng hợp. Test cũng
đảm bảo mỗi system input và provider input chỉ được sử dụng tối đa một lần, đồng
thời mỗi scope chứa chính xác toàn bộ enum:

```python
assert set(actual_summary) == {item.value for item in ReconResultType}
```

Chạy generator hai lần với cùng seed phải tạo Mongo document, provider CSV và
reconciliation oracle giống nhau từng byte. Đổi seed sẽ đổi ID và timestamp
nhưng giữ nguyên ma trận mutation cùng số lượng expected result.
