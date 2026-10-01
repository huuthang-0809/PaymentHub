# F07 — Business calendar, cutoff và allowed lateness

## 1. Overview

| Item | Detail |
|---|---|
| Feature | Business calendar, cutoff và allowed lateness |
| Owner | `[TBD]` |
| Ticket | `[TBD]` |
| Status | `Partially implemented — timezone/cutoff có quy ước tạm; allowed lateness chờ xác nhận` |
| Purpose | Xác định đúng reconciliation period theo provider/tenant thay vì luôn dùng local midnight, đồng thời phân loại late-arriving transactions. |

## 2. Requirements

### Functional Requirements

- Resolve timezone, cutoff time và allowed lateness theo region/tenant/provider và business date.
- Tạo UTC start/end, cutoff instant và lateness boundary có xử lý DST đúng.
- Source query theo resolved business period thay vì hard-code midnight.
- Phân loại/lưu `late_arriving` cho provider/system records theo rule được duyệt.
- Persist timezone, cutoff và allowed lateness đã áp dụng để audit/replay deterministic.

Quy ước tạm đang triển khai: `business_date=D` là kỳ nửa mở
`[(D-1) cutoff, D cutoff)` trong timezone IANA nhận từ input. Provider row ngoài
kỳ không tham gia matching của run D; vẫn lưu row để audit. Row tại hoặc sau
cutoff có `late_arriving=true`; row trước đầu kỳ không được đánh dấu muộn.
Khi chạy kỳ sau, repository đọc các late row từ run đã `COMPLETED` có timestamp
nằm trong kỳ mới và cùng region/tenant/provider, rồi gộp chúng với statement
mới trước matching. Nếu statement mới gửi lại cùng định danh, dòng trong
statement mới được ưu tiên. Carry-over giữ source file/row và `late_arriving`
để truy vết. Bản tối giản chưa dùng allowed lateness và không mở lại kỳ đích đã
`COMPLETED`. Không thêm migration hoặc đổi khóa idempotency. Rerun cùng input
nhưng khác timezone/cutoff bị từ chối để bảo toàn policy của run đã lưu.

### Non-Functional Requirements

- Policy phải versioned hoặc snapshot vào run; config thay đổi không được làm replay lịch sử đổi nghĩa.
- Dùng timezone database/IANA names.
- Không tự giả định ngày lễ, weekend hoặc provider settlement calendar.

## 3. Design

### Flow

```mermaid
sequenceDiagram
    autonumber
    participant Trigger as Scheduler / API
    participant Calendar as BusinessCalendarPolicy
    participant Orch as Reconciliation Orchestrator
    participant PG as PostgreSQL
    participant Source as SystemTransactionSource
    participant Mongo as MongoDB

    Trigger->>Calendar: Resolve scope and target business_date
    Calendar->>Calendar: Apply timezone, cutoff and allowed lateness
    Note over Trigger,Calendar: Period boundaries and lateness semantics await confirmation in section 8
    Calendar-->>Trigger: Resolved period only after business rules are confirmed
    Trigger->>Orch: Execute target business_date under confirmed rules
    Orch->>PG: Load completed late rows whose timestamp is in target period
    PG-->>Orch: Carry-over provider rows
    Orch->>Orch: Merge with current statement; current file wins duplicates
    Orch->>Source: Load transactions in half-open UTC period
    Source->>Mongo: Query timestamp >= start and < end
    Mongo-->>Source: Raw system documents
    Source-->>Orch: Canonical transactions
    Orch->>Orch: Classify provider/system late-arriving records
    Orch->>Orch: Persist applied timezone, cutoff and allowed lateness
```

### Processing

1. Policy registry resolve versioned calendar config cho run scope.
2. Calendar service tính aware local instants rồi convert UTC boundaries.
3. Repository đọc late provider rows từ các run hoàn tất trước đó theo scope và
   UTC boundaries của kỳ đích.
4. Orchestrator gộp carry-over với file hiện tại, matching và persist lineage
   cùng applied policy + late flags.

### Components

| Component | Change |
|---|---|
| `BusinessCalendarPolicy` port | New. |
| Provider/tenant policy registry | New; source of truth `[TBD]`. |
| `SystemTransactionSource` | Change input từ `business_date` sang resolved period. |
| `ReconciliationCommand` / persistence | Add applied timezone/cutoff/lateness/version. |
| `ReconRepository` | Đọc late provider rows của run hoàn tất để carry-over sang kỳ chứa timestamp. |

## 4. API & Data

### API

| Method | Endpoint | Description |
|---|---|---|
| POST | `/reconciliation-runs` | Nhận thêm hai form field bắt buộc `business_timezone` và `cutoff_time`. |

### Request/Response

- Request có business date + scope + `business_timezone` (ví dụ `Asia/Ho_Chi_Minh`) + `cutoff_time` (ví dụ `02:00:00`).
- Response/query view nên expose applied timezone, cutoff, allowed lateness và policy version.

### Database

| Table / Collection | Change |
|---|---|
| `recon_run` | Ghi timezone/cutoff từ input vào các cột sẵn có; không sử dụng allowed lateness. |
| `provider_txn.late_arriving` | Runtime set `true` cho provider row tại hoặc sau cutoff của run. |
| System late evidence | Schema/reference `[TBD]`. |

## 5. Error & Edge Cases

| Scenario | Expected Behavior |
|---|---|
| Happy path | Query/classify theo exact resolved business period. |
| Invalid input | Unknown timezone, negative lateness hoặc missing policy fail với stable error. |
| Duplicate request | Applied policy snapshot là một phần identity/replay decision `[Need Confirm]`. |
| Timeout / dependency error | Policy store unavailable phải fail run; fallback config chỉ dùng nếu được phê duyệt. |
| Retry / concurrent processing | Retry cùng run dùng policy snapshot ban đầu. |

## 6. Testing

| Test Case | Expected Result |
|---|---|
| Non-midnight cutoff | UTC boundaries đúng và không mất/nhân đôi row. |
| DST transition | Period được tính từ timezone-aware local instants. |
| Lateness boundary | Rows ngay trước/tại/sau boundary được phân loại rõ ràng. |
| Retry after config change | Vẫn dùng policy snapshot/version của run. |
| Late row thuộc kỳ kế tiếp | Được đọc từ `provider_txn`, gộp và matching trong run kế tiếp. |
| File kỳ mới gửi lại late row | Chỉ một provider row tham gia matching; ưu tiên dòng file mới. |

## 7. Open Questions

| Question | Owner | Status |
|---|---|---|
| Cutoff/lateness rule của từng provider/tenant là gì? | Finance/Operations | `[Need Confirm]` |
| Boundary là inclusive hay exclusive? | Finance | `[Need Confirm]` |
| Có business holiday calendar hay chỉ calendar day? | Operations | `[Need Confirm]` |
| Late row được đưa vào run hiện tại, run kế tiếp hay report riêng? | Product/Finance | `Assumption implemented: kỳ chứa transaction_time` |

## 8. Câu hỏi cần xác nhận trước khi triển khai cutoff/allowed lateness

Sequence diagram mô tả thiết kế đầy đủ còn chờ xác nhận. Bản tối giản đã dùng
quy ước tạm ở mục 2; các rule production và allowed lateness vẫn cần xác nhận.

| Question | Owner | Status |
|---|---|---|
| `business_date` là ngày bắt đầu hay ngày kết thúc kỳ? Ví dụ cutoff 02:00 thì kỳ D là `[D 02:00, D+1 02:00)` hay `[D-1 02:00, D 02:00)`? | Technical Lead / Provider team | `[Need Confirm]` |
| Cutoff so sánh với timestamp nào: thời điểm tạo payment, provider ghi nhận hay thời điểm dữ liệu đến hệ thống? | Technical Lead / Provider team | `[Need Confirm]` |
| `allowed_lateness` dùng để chờ trước khi chốt, nhận bổ sung vào kỳ cũ hay xác định chuyển sang kỳ sau? Mốc bắt đầu tính là gì? | Technical Lead / Finance | `[Need Confirm]` |
| Payment có event time trước cutoff nhưng đến sau cutoff, còn trong allowed lateness, được đưa vào kỳ nào? | Finance / Provider team | `[Need Confirm]` |
