from datetime import UTC, datetime, timedelta, timezone

import pytest

from canonical.adapters.vn import VNMapping, vn_to_canonical
from shared_kernel import AmountStatus, CanonicalStatus, TxnType
from shared_kernel.enums import Instrument


def valid_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "_id": "vn-1",
        "Amount": "100000",
        "BillIds": ["bill-1", "bill-2"],
        "CreatedOn": datetime(2026, 9, 22, tzinfo=UTC),
        "UpdatedOn": "2026-09-22T08:00:00+07:00",
        "CustomerId": "customer-1",
        "IsDeleted": False,
        "IsSentCallback": True,
        "MerchantId": "merchant-1",
        "Message": None,
        "PaidDate": "2026-09-22T08:01:00+07:00",
        "PaymentMethod": "CARD",
        "ServiceType": "PAYMENT",
        "Status": 1,
        "PaymentTransactionId": "onepay-1",
        "PollCount": 2,
    }
    document.update(overrides)
    return document


def mapping() -> VNMapping:
    return VNMapping(
        instrument_by_method={"CARD": Instrument.CARD},
        card_link_methods=frozenset({"LINKCARD"}),
    )


def test_happy_payment_mapping() -> None:
    transaction = vn_to_canonical(valid_document(), mapping())

    assert transaction.region.value == "VN"
    assert transaction.tenant_id is None
    assert transaction.txn_type is TxnType.PAYMENT
    assert transaction.instrument is Instrument.CARD
    assert transaction.provider == "ONEPAY"
    assert transaction.status is CanonicalStatus.SUCCESS
    assert transaction.amount_minor == 100000
    assert transaction.currency == "VND"
    assert transaction.amount_status is AmountStatus.ASSUMED_CURRENCY
    assert transaction.provider_txn_id == "onepay-1"
    assert transaction.merchant_ref == "vn-1"
    assert transaction.order_ids == ("bill-1", "bill-2")
    assert transaction.callback_attempts == 1
    assert transaction.provider_poll_count == 2
    assert transaction.updated_at == datetime(2026, 9, 22, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("source_status", "expected"),
    [
        (0, CanonicalStatus.PENDING),
        (1, CanonicalStatus.SUCCESS),
        (2, CanonicalStatus.FAILED),
        (3, CanonicalStatus.SUCCESS),
        (4, CanonicalStatus.REFUNDED),
        (5, CanonicalStatus.UNKNOWN),
        (6, CanonicalStatus.CANCELLED),
        (99, CanonicalStatus.UNKNOWN),
        (True, CanonicalStatus.UNKNOWN),
        ("1", CanonicalStatus.UNKNOWN),
    ],
)
def test_status_mapping(source_status: object, expected: CanonicalStatus) -> None:
    transaction = vn_to_canonical(valid_document(Status=source_status), mapping())
    assert transaction.status is expected


def test_refund_uses_order_info_as_parent_reference() -> None:
    transaction = vn_to_canonical(
        valid_document(PaymentMethod="Refund", OrderInfo="parent-1", Status=4),
        mapping(),
    )
    assert transaction.txn_type is TxnType.REFUND
    assert transaction.refund_of == "parent-1"
    assert transaction.is_reconcilable is True


def test_card_link_is_not_a_monetary_or_reconcilable_transaction() -> None:
    transaction = vn_to_canonical(
        valid_document(PaymentMethod="LinkCard", Amount=None), mapping()
    )
    assert transaction.txn_type is TxnType.CARD_LINK
    assert transaction.amount_status is AmountStatus.NOT_APPLICABLE
    assert transaction.amount_minor is None
    assert transaction.is_money_movement is False
    assert transaction.is_reconcilable is False


@pytest.mark.parametrize("amount", [None, "not-a-number", "10.5", 1.5])
def test_invalid_vnd_amount_is_visible(amount: object) -> None:
    transaction = vn_to_canonical(valid_document(Amount=amount), mapping())
    assert transaction.amount_status is AmountStatus.INVALID
    assert transaction.amount_minor is None


def test_source_flags_and_failure_reason_are_preserved() -> None:
    transaction = vn_to_canonical(
        valid_document(IsDeleted=True, IsSentCallback=False, Message="declined"),
        mapping(),
    )
    assert transaction.is_deleted is True
    assert transaction.callback_attempts == 0
    assert transaction.failure_reason == "declined"


def test_timestamp_is_converted_to_utc() -> None:
    local_timezone = timezone(timedelta(hours=7))
    transaction = vn_to_canonical(
        valid_document(CreatedOn=datetime(2026, 9, 22, 8, tzinfo=local_timezone)),
        mapping(),
    )
    assert transaction.created_at == datetime(2026, 9, 22, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({"CreatedOn": datetime(2026, 9, 22, tzinfo=UTC)}, "missing _id"),
        ({"_id": "vn-1"}, "missing CreatedOn"),
    ],
)
def test_required_fields(document: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        vn_to_canonical(document, mapping())


def test_bill_ids_and_poll_count_are_validated() -> None:
    with pytest.raises(ValueError, match="BillIds must be a list"):
        vn_to_canonical(valid_document(BillIds="bill-1"), mapping())
    with pytest.raises(ValueError, match="PollCount must be an integer"):
        vn_to_canonical(valid_document(PollCount=True), mapping())
