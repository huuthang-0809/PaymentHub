from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from shared_kernel.enums import (
    AmountStatus,
    CanonicalStatus,
    Instrument,
    Region,
    TxnType
)
from canonical.domain import CanonicalTransaction
from shared_kernel import Money, ensure_utc, parse_iso_utc

STATUSES = { # Mapping from documents
    0: CanonicalStatus.CREATED,
    1: CanonicalStatus.PENDING,
    2: CanonicalStatus.SUCCESS,
    3: CanonicalStatus.FAILED,
    4: CanonicalStatus.REFUNDED,
    5: CanonicalStatus.PARTIAL_REFUNDED,
}
TERMINAL = {
    CanonicalStatus.SUCCESS,
    CanonicalStatus.FAILED,
    CanonicalStatus.REFUNDED,
    CanonicalStatus.PARTIAL_REFUNDED,
}

@dataclass(frozen=True, slots=True)
class AsiaMapping:
    tenant_providers: Mapping[str, str] = field(
        default_factory=lambda: {"asia-id": "ESPAY", "asia-ph": "2C2P"}
    )
    tenant_currencies: Mapping[str, str] = field(
        default_factory=lambda: {"asia-id": "IDR", "asia-ph": "PHP"}
    )
    type_code: Mapping[int, TxnType] = field(default_factory=dict)
    # instrument_code: Mapping[int, Instrument] = field(default_factory=dict)
    synthetic_flag_value: Mapping[str, bool] = field(default_factory=dict) #unverify isSyntheticTestData values

def _optional_string(value: str) -> str| None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None

def _type_of(document: Mapping[str, Any], config: AsiaMapping) -> TxnType:
    value = document.get("Type")
    if isinstance(value, int) and not isinstance(value, bool):
        return config.type_code.get(value, TxnType.UNKNOWN)

    return TxnType.UNKNOWN

def _optional_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return parse_iso_utc(value)
    if isinstance(value, datetime):
        return ensure_utc(value)
    raise ValueError(f"Invalid timestamp {value}")

def _is_test_data(document: Mapping[str, Any], config: AsiaMapping) -> bool:
    flag = document.get("isSyntheticTestData")
    if flag is None:
        return False
    if isinstance(flag, str) and flag in config.synthetic_flag_value:
        return config.synthetic_flag_value[flag]
    raise ValueError("Unverified Asia synthetic-data flag")

def asia_to_canonical(document: Mapping[str, Any], tenant_id: str, config: AsiaMapping | None = None) -> CanonicalTransaction:
    config = config or AsiaMapping()
    if tenant_id not in config.tenant_providers or tenant_id not in config.tenant_currencies:
        raise ValueError(f"Unconfigured Asia tenant_id : {tenant_id}")
    if document.get("_id") is None:
        raise ValueError("missing _id")
    created_at = _optional_time(document.get("CreatedAt"))
    if created_at is None:
        raise ValueError("missing CreatedAt")
    txn_type = _type_of(document, config)
    source_status = document.get("Status")
    status = (
        STATUSES.get(source_status, CanonicalStatus.UNKNOWN)
        if isinstance(source_status, int) and not isinstance(source_status, bool)
        else CanonicalStatus.UNKNOWN
    )

    currency_raw = _optional_string(document.get("Currency"))
    currency = currency_raw.upper() if currency_raw is not None else None
    amount_minor: int | None = None
    amount_status = AmountStatus.OK
    if txn_type in (TxnType.CARD_LINK, TxnType.TOKENIZATION):
        amount_status = AmountStatus.NOT_APPLICABLE
    elif currency is None or currency not in {"IDR", "PHP"}:
        amount_status = AmountStatus.NO_CURRENCY
    else:
        try:
            primary = Money(document["AmountAsDecimal"], currency)
            if (
                document.get("Amount") is not None
                and Money(document["Amount"], currency) != primary
            ):
                amount_status = AmountStatus.INVALID
            else:
                amount_minor = primary.to_minor()
        except (KeyError, TypeError, ValueError):
            amount_status = AmountStatus.INVALID

    refund_of = _optional_string(document.get("RefundFor"))

    provider_txn_id = (
        None
        if txn_type is TxnType.REFUND or refund_of is not None
        else _optional_string(document.get("ExternalTxnId"))
    )
    bill_ids = document.get("BillIds") or []
    if not isinstance(bill_ids, (list, tuple)):
        raise ValueError("BillIds nust be a list")

    return CanonicalTransaction(
        region=Region.ASIA,
        tenant_id=tenant_id,
        txn_id = (document["_id"]),
        txn_type=txn_type,
        instrument = None, # unverify
        provider=config.tenant_providers[tenant_id],
        status=status,
        amount_status=amount_status,
        created_at=created_at,
        is_terminal= status in TERMINAL,
        is_money_movement=txn_type in {TxnType.PAYMENT, TxnType.LOAD, TxnType.REFUND} 
        and status in {CanonicalStatus.SUCCESS, CanonicalStatus.REFUNDED, CanonicalStatus.PARTIAL_REFUNDED},
        is_reconcilale=txn_type not in {TxnType.CARD_LINK, TxnType.TOKENIZATION, TxnType.UNKNOWN},
        provider_txn_id=provider_txn_id,
        provider_ref_id=_optional_string(document.get("PaymentRefId")),
        merchant_ref=str(document["_id"]),
        order_ids=tuple(str(item) for item in bill_ids),
        channel_raw=_optional_string(document.get("Channel")),
        merchant_id=_optional_string(document.get("MerchantCode")),
        provider_merchant_id=_optional_string(document.get("MerchantRefCode")),
        service_id=_optional_string(document.get("ServiceId")),
        customer_id=_optional_string(document.get("CustomerId")),
        amount_minor=amount_minor,
        currency=currency,
        refund_of=refund_of,
        updated_at=_optional_time(document.get("UpdatedAt")),
        paid_at=_optional_time(document.get("PaidDate")),
        callback_attempts=int(document.get("IsSentCallback") is True),
        is_deleted=document.get("IsDeleted") is True,
        is_test_data=_is_test_data(document, config),
        raw=dict(document),
    )