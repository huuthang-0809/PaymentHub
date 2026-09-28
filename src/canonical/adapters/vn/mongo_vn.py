from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from canonical.domain import CanonicalTransaction
from shared_kernel import Money, ensure_utc, parse_iso_utc
from shared_kernel.enums import (
    AmountStatus,
    CanonicalStatus,
    Instrument,
    Region,
    TxnType,
)


STATUSES = {
    0: CanonicalStatus.PENDING,
    1: CanonicalStatus.SUCCESS,
    2: CanonicalStatus.FAILED,
    3: CanonicalStatus.SUCCESS,
    4: CanonicalStatus.REFUNDED,
    5: CanonicalStatus.UNKNOWN,
    6: CanonicalStatus.CANCELLED,
}

TERMINAL_STATUSES = {
    CanonicalStatus.SUCCESS,
    CanonicalStatus.FAILED,
    CanonicalStatus.REFUNDED,
    CanonicalStatus.PARTIAL_REFUNDED,
    CanonicalStatus.CANCELLED,
}


@dataclass(frozen=True, slots=True)
class VNMapping:
    provider: str = "ONEPAY"
    assumed_currency: str = "VND"
    refund_method: str = "REFUND"
    instrument_by_method: Mapping[str, Instrument] = field(default_factory=dict)
    card_link_methods: frozenset[str] = field(default_factory=frozenset)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _optional_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return ensure_utc(value)
    if isinstance(value, str):
        return parse_iso_utc(value)
    raise ValueError(f"Invalid timestamp: {value}")


def _money(value: Any, currency: str) -> Money:
    to_decimal = getattr(value, "to_decimal", None)
    if callable(to_decimal):
        value = to_decimal()
    return Money(value, currency)


def _integer_or_none(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _string_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list")
    return tuple(str(item) for item in value)


def _transaction_type(method: str | None, config: VNMapping) -> TxnType:
    normalized = method.upper() if method else ""
    if normalized == config.refund_method.strip().upper():
        return TxnType.REFUND
    if normalized in {item.strip().upper() for item in config.card_link_methods}:
        return TxnType.CARD_LINK
    return TxnType.PAYMENT


def vn_to_canonical(
    document: Mapping[str, Any], config: VNMapping | None = None
) -> CanonicalTransaction:
    config = config or VNMapping()
    if document.get("_id") is None:
        raise ValueError("missing _id")

    created_at = _optional_time(document.get("CreatedOn"))
    if created_at is None:
        raise ValueError("missing CreatedOn")

    method = _optional_string(document.get("PaymentMethod"))
    normalized_method = method.upper() if method else ""
    txn_type = _transaction_type(method, config)
    source_status = document.get("Status")
    status = (
        STATUSES.get(source_status, CanonicalStatus.UNKNOWN)
        if isinstance(source_status, int) and not isinstance(source_status, bool)
        else CanonicalStatus.UNKNOWN
    )

    currency = config.assumed_currency.strip().upper()
    amount_minor: int | None = None
    amount_status = AmountStatus.NOT_APPLICABLE
    if txn_type is not TxnType.CARD_LINK:
        try:
            amount_minor = _money(document["Amount"], currency).to_minor()
            amount_status = AmountStatus.ASSUMED_CURRENCY
        except (KeyError, TypeError, ValueError):
            amount_status = AmountStatus.INVALID

    refund_of = (
        _optional_string(document.get("OrderInfo"))
        if txn_type is TxnType.REFUND
        else None
    )
    is_money_movement = (
        txn_type in {TxnType.PAYMENT, TxnType.REFUND}
        and status
        in {
            CanonicalStatus.SUCCESS,
            CanonicalStatus.REFUNDED,
            CanonicalStatus.PARTIAL_REFUNDED,
        }
    )

    return CanonicalTransaction(
        region=Region.VN,
        tenant_id=None,
        txn_id=str(document["_id"]),
        txn_type=txn_type,
        instrument=config.instrument_by_method.get(normalized_method),
        provider=config.provider,
        status=status,
        amount_status=amount_status,
        created_at=created_at,
        is_terminal=status in TERMINAL_STATUSES,
        is_money_movement=is_money_movement,
        is_reconcilable=txn_type in {TxnType.PAYMENT, TxnType.REFUND},
        provider_txn_id=_optional_string(document.get("PaymentTransactionId")),
        merchant_ref=str(document["_id"]),
        order_ids=_string_tuple(document.get("BillIds"), "BillIds"),
        channel_raw=method,
        merchant_id=_optional_string(document.get("MerchantId")),
        service_id=_optional_string(document.get("ServiceType")),
        customer_id=_optional_string(document.get("CustomerId")),
        amount_minor=amount_minor,
        currency=currency,
        refund_of=refund_of,
        updated_at=_optional_time(document.get("UpdatedOn")),
        paid_at=_optional_time(document.get("PaidDate")),
        failure_reason=_optional_string(document.get("Message")),
        callback_attempts=int(document.get("IsSentCallback") is True),
        provider_poll_count=_integer_or_none(document.get("PollCount"), "PollCount"),
        is_deleted=document.get("IsDeleted") is True,
        is_test_data=False,
        raw=dict(document),
    )
