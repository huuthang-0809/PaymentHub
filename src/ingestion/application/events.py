from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID
from shared_kernel import Region

@dataclass(frozen=True, slots=True)
class PaymentUpdated:
    event_id: UUID
    region: str
    tenant_id: str | None
    txn_id: str
    txn_type: str
    provider: str
    status: str
    amount_minor: int | None
    currency: str | None
    updated_at: datetime
    is_late: bool
    provider_txn_id: str | None = None
    provider_ref_id: str | None = None
    merchant_ref: str | None = None
    refund_of: str | None = None

@dataclass(frozen=True, slots=True)
class StatementRow:
    event_id: UUID
    region: Region
    tenant_id: str | None
    provider: str
    business_date: date
    provider_txn_id: str | None
    provider_ref_id: str | None
    merchant_ref: str | None
    amount_minor: int
    currency: str
    occurred_at: datetime
    source_file: str
    source_row: int

type IngestEvent = PaymentUpdated | StatementRow