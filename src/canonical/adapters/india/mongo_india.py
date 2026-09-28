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


STATUS_BY_TYPE = {
    (TxnType.LOAD, "created"): CanonicalStatus.CREATED,
    (TxnType.LOAD, "success"): CanonicalStatus.SUCCESS,
    (TxnType.LOAD, "failed"): CanonicalStatus.FAILED,
    (TxnType.PAYMENT, "pending"): CanonicalStatus.PENDING,
    (TxnType.PAYMENT, "authorized"): CanonicalStatus.PENDING,
    (TxnType.PAYMENT, "captured"): CanonicalStatus.SUCCESS,
    (TxnType.PAYMENT, "cancelled"): CanonicalStatus.CANCELLED,
    (TxnType.PAYMENT, "failed"): CanonicalStatus.FAILED,
    (TxnType.PAYMENT, "refunded"): CanonicalStatus.REFUNDED,
    (TxnType.REFUND, "created"): CanonicalStatus.CREATED,
    (TxnType.REFUND, "pending"): CanonicalStatus.PENDING,
    (TxnType.REFUND, "processed"): CanonicalStatus.SUCCESS,
    (TxnType.REFUND, "success"): CanonicalStatus.SUCCESS,
    (TxnType.REFUND, "failed"): CanonicalStatus.FAILED,
    (TxnType.REFUND, "reversed"): CanonicalStatus.FAILED,
    (TxnType.REFUND, "reversal_failed"): CanonicalStatus.FAILED,
}


@dataclass(frozen=True, slots=True)
class IndiaMapping:
    provider: str = "RAZORPAY"
    currency: str = "INR"
    instrument_by_type: Mapping[str, Instrument] = field(
        default_factory=lambda: {
            "card": Instrument.CARD,
            "virtual_account": Instrument.VIRTUAL_ACCOUNT,
            "ewallet": Instrument.EWALLET,
            "qr": Instrument.QR,
            "payment_link": Instrument.PAYMENT_LINK,
            "direct_debit": Instrument.DIRECT_DEBIT,
        }
    )


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


def _transaction_type(value: Any) -> TxnType:
    normalized = _optional_string(value)
    if normalized is None:
        return TxnType.UNKNOWN
    try:
        return TxnType(normalized.upper())
    except ValueError:
        return TxnType.UNKNOWN


def _callback_attempts(document: Mapping[str, Any]) -> int:
    callback_log = document.get("merchant_callback_log")
    if callback_log is not None:
        if not isinstance(callback_log, (list, tuple)):
            raise ValueError("merchant_callback_log must be a list")
        return len(callback_log)

    callback_error = document.get("callback_error")
    if callback_error is None:
        return 0
    if not isinstance(callback_error, Mapping):
        raise ValueError("callback_error must be an object")
    attempts = callback_error.get("attempts", 0)
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        raise ValueError("callback_error.attempts must be a non-negative integer")
    return attempts


def india_to_canonical(
    document: Mapping[str, Any], config: IndiaMapping | None = None
) -> CanonicalTransaction:
    config = config or IndiaMapping()
    if document.get("_id") is None:
        raise ValueError("missing _id")

    created_at = _optional_time(document.get("createdAt"))
    if created_at is None:
        raise ValueError("missing createdAt")
    if not isinstance(document.get("terminal"), bool):
        raise ValueError("terminal must be a boolean")

    txn_type = _transaction_type(document.get("type"))
    raw_status = _optional_string(document.get("status"))
    status = STATUS_BY_TYPE.get(
        (txn_type, raw_status.lower() if raw_status else ""),
        CanonicalStatus.UNKNOWN,
    )

    source_currency = _optional_string(document.get("currency"))
    currency = source_currency.upper() if source_currency else None
    expected_currency = config.currency.strip().upper()
    amount_minor: int | None = None
    refunded_amount_minor: int | None = None
    if currency is None:
        amount_status = AmountStatus.NO_CURRENCY
    elif currency != expected_currency:
        amount_status = AmountStatus.INVALID
    else:
        try:
            amount_minor = _money(document["amount"], currency).to_minor()
            if document.get("amountRefunded") is not None:
                refunded_amount_minor = _money(
                    document["amountRefunded"], currency
                ).to_minor()
            amount_status = AmountStatus.OK
        except (KeyError, TypeError, ValueError):
            amount_status = AmountStatus.INVALID
            amount_minor = None
            refunded_amount_minor = None

    instrument_raw = _optional_string(document.get("instrumentType"))
    instrument = (
        config.instrument_by_type.get(instrument_raw.lower())
        if instrument_raw
        else None
    )
    source_ids = _string_tuple(document.get("sourceIds"), "sourceIds")
    merchant_ref = (
        _optional_string(document.get("referenceId"))
        if txn_type in {TxnType.LOAD, TxnType.REFUND}
        else None
    )
    is_money_movement = (
        txn_type in {TxnType.LOAD, TxnType.PAYMENT, TxnType.REFUND}
        and status in {CanonicalStatus.SUCCESS, CanonicalStatus.REFUNDED}
    )

    return CanonicalTransaction(
        region=Region.INDIA,
        tenant_id=None,
        txn_id=str(document["_id"]),
        txn_type=txn_type,
        instrument=instrument,
        provider=config.provider,
        status=status,
        amount_status=amount_status,
        created_at=created_at,
        is_terminal=document["terminal"],
        is_money_movement=is_money_movement,
        is_reconcilable=txn_type in {TxnType.LOAD, TxnType.PAYMENT, TxnType.REFUND},
        provider_txn_id=_optional_string(document.get("transactionId")),
        merchant_ref=merchant_ref,
        order_ids=source_ids,
        channel_raw=instrument_raw,
        merchant_id=_optional_string(document.get("merchantId")),
        service_id=_optional_string(document.get("serviceType")),
        customer_id=_optional_string(document.get("customerId")),
        amount_minor=amount_minor,
        currency=currency,
        refund_of=_optional_string(document.get("relatedPaymentId")),
        refunded_amount_minor=refunded_amount_minor,
        updated_at=_optional_time(document.get("updatedAt")),
        paid_at=_optional_time(document.get("paidAt")),
        provider_created_at=_optional_time(document.get("gatewayCreatedAt")),
        failure_reason=_optional_string(document.get("failureReason")),
        callback_attempts=_callback_attempts(document),
        provider_poll_count=_integer_or_none(
            document.get("reconcileAttempts"), "reconcileAttempts"
        ),
        is_deleted=False,
        is_test_data=False,
        raw=dict(document),
    )
