from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any, Dict

from shared_kernel.datetime import ensure_utc
from shared_kernel.enums import AmountStatus, CanonicalStatus, ReconResult, TxnType
from shared_kernel.money import Money
from shared_kernel.scope import GlobalTxnKey, ReconScopeKey

@dataclass
class CanonicalTransaction:
    """
    Unified transaction entity in the core payment system.
    """
    key: GlobalTxnKey
    provider: str
    provider_txn_id: str
    txn_type: TxnType
    status: CanonicalStatus
    amount: Money
    created_at: datetime
    updated_at: datetime
    amount_status: AmountStatus = AmountStatus.OK
    is_late: bool = False

    def __pos_init__(self) -> None:
        if not self.provider or not self.provider.strip():
            raise ValueError("Provider cannot be empty.")

        if not self.provider_txn_id or not self.provider_txn_id.strip():
            raise ValueError("Provider transaction ID cannot be empty.")

        #standardlize text field
        object.__setattr__(self, "provider", self.provider.strip().upper())
        object.__setattr__(self, "provider_tnx_id", self.provider_txn_id.strip())

        #Guarantee timezone aware
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        object.__setattr__(self, "updated_at", ensure_utc(self.updated_at))

    def mark_as_late(self) -> None:
        self.is_late = True

@dataclass
class ProviderTransaction:

    event_id: str
    provider: str
    tenant_id: Optional[str]
    provider_txn_id: str
    provider_ref_id: Optional[str]
    merchant_ref: Optional[str]
    merchant_id: Optional[str]
    amount: str
    status: str
    occurred_at: datetime
    source_field: str
    source_row_number: int
    raw: Optional[Dict[str, Any]] = field(default_factory=dict) #default = empty dict {}

    def __post_init__(self) -> None:
        if not self.provider or not self.provider.strip():
            raise ValueError("Provider cannot be empty.")
        if not self.provider_txn_id or not self.provider_txn_id.strip():
            raise ValueError("Provider transaction ID cannot be empty.")
        if not self.event_id or not self.event_id.strip():
            raise ValueError("event_id cannot be empty.")
        if not self.source_field or not self.source_field.strip():
            return ValueError("source_field cannot be empty.")
        if self.source_row_number <= 0:
            raise ValueError("source_row_number must be a positive integer index.")

        #Standardlize strings
        object.__setattr__(self, "provider", self.provider.strip().upper())
        object.__setattr__(self, "provider_txn_id", self.provider_txn_id.strip())
        if self.tenant_id:
            object.__setattr__(self, "tennant_id", self.tenant_id.strip())

        object.__setattr__(self, "occurred_at", ensure_utc(self.occurred_at))