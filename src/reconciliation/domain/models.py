from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from shared_kernel import Money, ReconScopeKey, Region, ensure_utc
from enum import Enum
from typing import Optional

@dataclass(frozen=True, slots=True)
class ProviderTransaction:
    provider: str
    region: str
    tenant_id: str | None
    provider_txn_id: str | None
    provider_ref_id: str | None
    merchant_ref: str | None
    merchant_id: str | None
    amount_minor: int
    currency: str
    status: str
    transaction_time: datetime
    source_file: str
    source_row_number: int
    raw: dict[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        if (not self.provider or not self.provider.strip()) or (not self.currency or not self.currency.strip()) or (not self.source_file or not self.source_file.strip()):
            raise ValueError("Provider, Currency and Source file are required")
        if self.region not in Region or (self.region == Region.ASIA and not self.tenant_id):
            raise ValueError("Invalid region or Asia tenant")

        Money.from_minor(self.amount_minor, self.currency)
        if self.amount_minor < 0:
            raise ValueError("Negative amount")
        if self.source_row_number < 1:
            raise ValueError("Source row number must be positive")
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
    """Conciliation result save in db recon_result"""
    run_id: str
    result_type: ReconResultType
    region: str
    tenant_id: Optional[str]
    provider: str
    currency: Optional[str]
    txn_type: Optional[str] = None
    
    # System info
    system_txn_id: Optional[str] = None
    system_amount_minor: Optional[int] = None
    
    # Provider info
    provider_txn_id: Optional[str] = None
    provider_amount_minor: Optional[int] = None
    source_file: Optional[str] = None
    source_row_number: Optional[int] = None
    
    delta_amount: Optional[int] = None
    reason: Optional[str] = None
