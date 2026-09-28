# F03 — Reconciliation application pipeline

## 1. Overview

| Item | Detail |
|---|---|
| Feature | Reconciliation application pipeline |
| Owner | `[TBD]` |
| Ticket | `[TBD]` |
| Status | `Implemented` |
| Purpose | Điều phối parse, scope validation, system load, filtering, matching, persistence và tạo command response. |

## 2. Requirements

### Functional Requirements

- Normalize requested provider sang uppercase và reject empty provider.
- Không cho statement row vượt requested region/tenant/provider.
- Load system transactions qua port theo business date.
- Loại `is_deleted` và `is_test_data` trước matching.
- Tạo `run_id` nếu caller không truyền.
- Lưu run rồi trả row counts, result counts và detailed result tuple cho internal caller.

### Non-Functional Requirements

- Application phụ thuộc ports thay vì concrete Mongo/PostgreSQL implementation.
- Composition root chọn source theo region và quản lý connection pools.
- Pipeline hiện synchronous; SLA và async/background execution `[Need Confirm]`.

## 3. Design

### Flow

```mermaid
sequenceDiagram
    autonumber
    actor Client
    participant API as Recon API
    participant UseCase as RunMatching
    participant Parser as Statement Parser
    participant Source as System Source
    participant Engine as MatchingEngine
    participant Repo as ReconRepository

    Client->>API: Submit statement and reconciliation scope
    API->>UseCase: Execute command
    UseCase->>Parser: Parse and validate statement
    Parser-->>UseCase: Provider rows
    UseCase->>Source: Load system rows for business date and scope
    Source-->>UseCase: Canonical transactions
    UseCase->>UseCase: Exclude deleted and test rows
    UseCase->>Engine: Match both datasets
    Engine-->>UseCase: Detailed results
    UseCase->>Repo: Persist run, provider rows and results
    Repo-->>UseCase: job_run_id
    UseCase-->>API: MatchingRunOutput and count summary
    API-->>Client: 201 Created
```

### Processing

1. Parse provider statement và validate command scope.
2. Load/filter system rows và invoke domain matching.
3. Persist provider rows/results, count classifications và return output.

### Components

| Component | Change |
|---|---|
| `RunMatching` | Đã triển khai orchestration hiện tại. |
| `SystemTransactionSource` | Đã triển khai port và regional implementations. |
| `ReconRepository` | Đã triển khai write port hiện tại. |
| `bootstrap.main.lifespan` | Đã triển khai dependency wiring và resource lifecycle. |

## 4. API & Data

### API

| Method | Endpoint | Description |
|---|---|---|
| POST | `/reconciliation-runs` | Entry point hiện tại của pipeline. |
| GET | `/health` | Health response tĩnh; chưa probe dependencies. |

### Request/Response

- Xem F01 cho HTTP contract.
- Internal output còn có `results`, nhưng HTTP response chỉ expose summary.

### Database

| Table / Collection | Change |
|---|---|
| Regional MongoDB | Read system transactions. |
| Reconciliation PostgreSQL tables | Write qua F04. |

## 5. Error & Edge Cases

| Scenario | Expected Behavior |
|---|---|
| Happy path | Trả completed run summary sau khi DB commit. |
| Invalid input | Parse/scope/domain `ValueError` được API map sang 422. |
| Duplicate request | Chưa return existing run; xem F06. |
| Timeout / dependency error | Mongo/PostgreSQL error hiện thành unhandled 500. |
| Retry / concurrent processing | Chưa có idempotent rerun/concurrency control của F06. |

## 6. Testing

| Test Case | Expected Result |
|---|---|
| Port-based happy path | Source request, persisted payload và output counts đúng. |
| Deleted/test filtering | Rows bị loại không đi vào matching. |
| Invalid statement scope | Fail trước system matching/persistence. |
| Composition smoke test | `[TBD]` — chưa có integration test với real dependencies. |

## 7. Open Questions

| Question | Owner | Status |
|---|---|---|
---
