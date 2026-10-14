from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Any, Dict

from shared_kernel.datetime import ensure_utc
from shared_kernel.enums import AmountStatus, CanonicalStatus, ReconResult, TxnType, Instrument, Region
from shared_kernel.money import Money
from shared_kernel.scope import GlobalTxnKey, ReconScopeKey

@dataclass(frozen=True, slots = True)
class CanonicalTransaction:
    """
    Unified transaction entity in the core payment system.
    """
    region: Region
    tenant_id: str | None
    txn_id: str
    txn_type: TxnType
    provider: str
    status: CanonicalStatus
    amount_status: AmountStatus
    created_at: datetime
    is_terminal: bool
    is_money_movement: bool
    is_reconcilable: bool
    instrument: Instrument | None = None
    provider_txn_id: str | None = None
    provider_ref_id: str | None = None
    merchant_ref: str | None = None
    order_ids: tuple[str, ...] = ()
    channel_raw: str | None = None
    merchant_id: str | None = None
    provider_merchant_id: str | None = None
    service_id: str | None = None
    customer_id: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    refund_of: str | None = None
    refunded_amount_minor: int | None = None
    updated_at: datetime | None = None
    paid_at: datetime | None = None
    provider_created_at: datetime | None = None
    failure_reason: str | None = None
    callback_attempts: int = 0
    provider_poll_count: int | None = None
    is_deleted: bool = False
    is_test_data: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.provider or not self.provider.strip():
            raise ValueError("Provider cannot be empty.")

        if not self.txn_id or not self.txn_id.strip():
            raise ValueError("txn_id cannot be empty")

        if self.region == Region.ASIA and not self.tenant_id:
            raise ValueError("ASIA requires tenant_id")

        if self.amount_minor is not None and (isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int)):
            raise TypeError("amount_minor must be integer")
        if self.callback_attempts < 0:
            raise ValueError("callback_attempt cannot be negative")
        

        #standardlize text field
        object.__setattr__(self, "provider", self.provider.strip().upper())
        if self.provider_txn_id is not None:
            object.__setattr__(
                self,
                "provider_txn_id",
                self.provider_txn_id.strip(),
            )

        #Guarantee timezone aware
        object.__setattr__(self, "created_at", ensure_utc(self.created_at))
        if self.updated_at is not None:
            object.__setattr__(self, "updated_at", ensure_utc(self.updated_at))

        if self.paid_at is not None:
            object.__setattr__(self, "paid_at", ensure_utc(self.paid_at))

        if self.provider_created_at is not None:
            object.__setattr__(
                self,
                "provider_created_at",
                ensure_utc(self.provider_created_at),
            )

    @property
    def global_key(self) -> GlobalTxnKey:
        return GlobalTxnKey(self.region, self.tenant_id, self.txn_id)

    @property
    def reconciliation_scope(self) -> ReconScopeKey | None:
        if self.currency is None or self.amount_status in {
            AmountStatus.NO_CURRENCY,
            AmountStatus.NOT_APPLICABLE,
        }:
            return None
        return ReconScopeKey(self.region, self.tenant_id, self.provider, self.currency)