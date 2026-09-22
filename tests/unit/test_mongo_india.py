from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from canonical.adapters.india import india_to_canonical
from shared_kernel import AmountStatus, CanonicalStatus, TxnType
from shared_kernel.enums import Instrument


class FakeDecimal128:
    def __init__(self, value: str) -> None:
        self.value = value

    def to_decimal(self) -> Decimal:
        return Decimal(self.value)


def valid_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "_id": "india-1",
        "amount": FakeDecimal128("10.50"),
        "amountRefunded": FakeDecimal128("1.25"),
        "createdAt": datetime(2026, 9, 22, tzinfo=UTC),
        "updatedAt": "2026-09-22T08:00:00+05:30",
        "currency": "INR",
        "customerId": "customer-1",
        "failureReason": None,
        "gatewayCreatedAt": "2026-09-22T02:31:00Z",
        "instrumentType": "card",
        "merchantId": "merchant-1",
        "paidAt": "2026-09-22T08:01:00+05:30",
        "reconcileAttempts": 3,
        "referenceId": "reference-1",
        "relatedPaymentId": None,
        "serviceType": "PAYMENT",
        "sourceIds": ["source-1", "source-2"],
        "status": "captured",
        "terminal": True,
        "transactionId": "razorpay-1",
        "type": "PAYMENT",
        "merchant_callback_log": [{"attempt": 1}, {"attempt": 2}],
    }
    document.update(overrides)
    return document


def test_happy_payment_mapping() -> None:
    transaction = india_to_canonical(valid_document())

    assert transaction.region.value == "INDIA"
    assert transaction.tenant_id is None
    assert transaction.txn_type is TxnType.PAYMENT
    assert transaction.status is CanonicalStatus.SUCCESS
    assert transaction.instrument is Instrument.CARD
    assert transaction.provider == "RAZORPAY"
    assert transaction.provider_txn_id == "razorpay-1"
    assert transaction.merchant_ref is None
    assert transaction.order_ids == ("source-1", "source-2")
    assert transaction.amount_minor == 1050
    assert transaction.refunded_amount_minor == 125
    assert transaction.amount_status is AmountStatus.OK
    assert transaction.callback_attempts == 2
    assert transaction.provider_poll_count == 3
    assert transaction.is_terminal is True
    assert transaction.updated_at == datetime(2026, 9, 22, 2, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("txn_type", "source_status", "expected"),
    [
        ("LOAD", "created", CanonicalStatus.CREATED),
        ("LOAD", "success", CanonicalStatus.SUCCESS),
        ("LOAD", "failed", CanonicalStatus.FAILED),
        ("PAYMENT", "pending", CanonicalStatus.PENDING),
        ("PAYMENT", "authorized", CanonicalStatus.PENDING),
        ("PAYMENT", "captured", CanonicalStatus.SUCCESS),
        ("PAYMENT", "cancelled", CanonicalStatus.CANCELLED),
        ("PAYMENT", "failed", CanonicalStatus.FAILED),
        ("PAYMENT", "refunded", CanonicalStatus.REFUNDED),
        ("REFUND", "created", CanonicalStatus.CREATED),
        ("REFUND", "pending", CanonicalStatus.PENDING),
        ("REFUND", "processed", CanonicalStatus.SUCCESS),
        ("REFUND", "success", CanonicalStatus.SUCCESS),
        ("REFUND", "failed", CanonicalStatus.FAILED),
        ("REFUND", "reversed", CanonicalStatus.FAILED),
        ("REFUND", "reversal_failed", CanonicalStatus.FAILED),
        ("PAYMENT", "new-status", CanonicalStatus.UNKNOWN),
        ("new-type", "success", CanonicalStatus.UNKNOWN),
    ],
)
def test_status_is_mapped_by_type(
    txn_type: str, source_status: str, expected: CanonicalStatus
) -> None:
    transaction = india_to_canonical(
        valid_document(type=txn_type, status=source_status)
    )
    assert transaction.status is expected


def test_terminal_flag_is_authoritative() -> None:
    transaction = india_to_canonical(
        valid_document(status="captured", terminal=False)
    )
    assert transaction.status is CanonicalStatus.SUCCESS
    assert transaction.is_terminal is False


def test_load_and_refund_use_reference_id() -> None:
    load = india_to_canonical(valid_document(type="LOAD", status="success"))
    refund = india_to_canonical(
        valid_document(
            type="REFUND",
            status="processed",
            relatedPaymentId="parent-provider-id",
        )
    )
    assert load.merchant_ref == "reference-1"
    assert refund.merchant_ref == "reference-1"
    assert refund.refund_of == "parent-provider-id"


def test_gateway_payment_id_is_not_used_as_provider_transaction_id() -> None:
    transaction = india_to_canonical(
        valid_document(transactionId=None, gatewayPaymentId="wrong-id")
    )
    assert transaction.provider_txn_id is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": "10.001"},
        {"amount": "not-a-number"},
        {"amount": 1.5},
        {"currency": "USD"},
        {"amountRefunded": "1.001"},
    ],
)
def test_invalid_amount_is_visible(overrides: dict[str, object]) -> None:
    transaction = india_to_canonical(valid_document(**overrides))
    assert transaction.amount_status is AmountStatus.INVALID
    assert transaction.amount_minor is None
    assert transaction.refunded_amount_minor is None


def test_missing_currency_is_visible() -> None:
    transaction = india_to_canonical(valid_document(currency=None))
    assert transaction.amount_status is AmountStatus.NO_CURRENCY
    assert transaction.amount_minor is None


def test_callback_log_takes_precedence_and_error_is_fallback() -> None:
    with_log = india_to_canonical(
        valid_document(
            merchant_callback_log=[{}, {}, {}],
            callback_error={"attempts": 9},
        )
    )
    fallback = india_to_canonical(
        valid_document(merchant_callback_log=None, callback_error={"attempts": 4})
    )
    assert with_log.callback_attempts == 3
    assert fallback.callback_attempts == 4


def test_timestamp_is_converted_to_utc() -> None:
    indian_timezone = timezone(timedelta(hours=5, minutes=30))
    transaction = india_to_canonical(
        valid_document(
            createdAt=datetime(2026, 9, 22, 8, tzinfo=indian_timezone)
        )
    )
    assert transaction.created_at == datetime(2026, 9, 22, 2, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({"createdAt": datetime(2026, 9, 22, tzinfo=UTC)}, "missing _id"),
        ({"_id": "india-1"}, "missing createdAt"),
    ],
)
def test_required_fields(document: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        india_to_canonical(document)


def test_terminal_source_ids_and_counters_are_validated() -> None:
    with pytest.raises(ValueError, match="terminal must be a boolean"):
        india_to_canonical(valid_document(terminal=1))
    with pytest.raises(ValueError, match="sourceIds must be a list"):
        india_to_canonical(valid_document(sourceIds="source-1"))
    with pytest.raises(ValueError, match="reconcileAttempts must be an integer"):
        india_to_canonical(valid_document(reconcileAttempts=True))
    with pytest.raises(ValueError, match="merchant_callback_log must be a list"):
        india_to_canonical(valid_document(merchant_callback_log={}))
