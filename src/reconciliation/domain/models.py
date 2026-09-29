from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from shared_kernel import (
    CanonicalStatus,
    Money,
    ReconScopeKey,
    Region,
    TxnType,
    ensure_utc,
)

@dataclass(frozen=True, slots=True)
class ProviderTransaction:
    provider: str
    region: Region
    tenant_id: str | None
    provider_txn_id: str | None
    provider_ref_id: str | None
    merchant_ref: str | None
    merchant_id: str | None
    amount_minor: int
    currency: str
    status: str
    txn_type: TxnType
    transaction_time: datetime
    source_file: str
    source_row_number: int
    raw: dict[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if (
            not self.provider
            or not self.provider.strip()
            or not self.currency
            or not self.currency.strip()
            or not self.source_file
            or not self.source_file.strip()
        ):
            raise ValueError("Provider, Currency and Source file are required")
        if self.region not in Region or (self.region == Region.ASIA and not self.tenant_id):
            raise ValueError("Invalid region or Asia tenant")

        provider = self.provider.strip().upper()
        currency = self.currency.strip().upper()
        Money.from_minor(self.amount_minor, currency)
        if self.amount_minor < 0:
            raise ValueError("Negative amount")
        if self.source_row_number < 1:
            raise ValueError("Source row number must be positive")

        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "transaction_time", ensure_utc(self.transaction_time))

    @property
    def reconciliation_scope(self) -> ReconScopeKey:
        return ReconScopeKey(self.region, self.tenant_id, self.provider, self.currency)

class ReconResultType(str, Enum):
    MATCHED = 'MATCHED'
    AMOUNT_MISMATCH = 'AMOUNT_MISMATCH'
    CURRENCY_MISMATCH = 'CURRENCY_MISMATCH'
    MISSING_IN_SYSTEM = 'MISSING_IN_SYSTEM'
    MISSING_IN_PROVIDER = 'MISSING_IN_PROVIDER'
    DUPLICATE = 'DUPLICATE'
    FUZZY_MATCHED = 'FUZZY_MATCHED'
    NOT_RECONCILABLE = 'NOT_RECONCILABLE'
    INVALID_DATA = 'INVALID_DATA'

@dataclass(frozen=True, slots = True)
class ReconResult:
    """Reconciliation outcome with amounts and currencies kept per side."""
    run_id: str
    result_type: ReconResultType
    region: Region
    tenant_id: str | None
    provider: str
    txn_type: TxnType | None = None
    
    # System info
    system_txn_id: str | None = None
    system_provider_txn_id: str | None = None
    system_status: CanonicalStatus | None = None
    system_amount_minor: int | None = None
    system_currency: str | None = None
    refund_of: str | None = None
    refunded_amount_minor: int | None = None
    
    # Provider info
    provider_txn_id: str | None = None
    provider_amount_minor: int | None = None
    provider_currency: str | None = None
    source_file: str | None = None
    source_row_number: int | None = None
    
    delta_amount: int | None = None
    reason: str | None = None
