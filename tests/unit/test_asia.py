from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from canonical.adapters.asia import AsiaMapping, asia_to_canonical
from canonical.domain import CanonicalTransaction
from shared_kernel import AmountStatus, CanonicalStatus, TxnType
from shared_kernel.enums import Instrument


SYNTHETIC_MAPPING = AsiaMapping(
    type_code={7: TxnType.PAYMENT, 8: TxnType.REFUND, 9: TxnType.CARD_LINK},
    instrument_code={"qr-method": Instrument.QR},
    synthetic_flag_value={"synthetic": True, "production": False},
)


class FakeDecimal128:
    def __init__(self, value: str) -> None:
        self.value = value

    def to_decimal(self) -> Decimal:
        return Decimal(self.value)


def valid_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "_id": "a1",
        "CreatedAt": datetime(2026, 9, 21, tzinfo=UTC),
        "UpdatedAt": "2026-09-21T08:00:00+07:00",
        "Status": 2,
        "Type": 7,
        "Currency": "IDR",
        "AmountAsDecimal": FakeDecimal128("100"),
        "Amount": "100",
        "ExternalTxnId": "e1",
        "PaymentRefId": "r1",
        "MerchantCode": "merchant",
        "MerchantRefCode": "provider-merchant",
        "PaymentMethodId": "qr-method",
        "BillIds": ["bill1", "bill2"],
        "IsSentCallback": True,
    }
    document.update(overrides)
    return document


def map_document(
    document: dict[str, object], tenant_id: str = "asia-id"
) -> CanonicalTransaction:
    return asia_to_canonical(document, tenant_id, SYNTHETIC_MAPPING)


def test_happy_mapping_normalizes_the_document() -> None:
    transaction = map_document(valid_document())

    assert (
        transaction.amount_minor,
        transaction.currency,
        transaction.amount_status,
    ) == (100, "IDR", AmountStatus.OK)
    assert transaction.status is CanonicalStatus.SUCCESS
    assert transaction.instrument is Instrument.QR
    assert transaction.provider == "ESPAY"
    assert transaction.order_ids == ("bill1", "bill2")
    assert transaction.callback_attempts == 1
    assert transaction.updated_at == datetime(2026, 9, 21, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("source_status", "canonical_status"),
    [
        (0, CanonicalStatus.CREATED),
        (1, CanonicalStatus.PENDING),
        (2, CanonicalStatus.SUCCESS),
        (3, CanonicalStatus.FAILED),
        (4, CanonicalStatus.REFUNDED),
        (5, CanonicalStatus.PARTIAL_REFUNDED),
        (999, CanonicalStatus.UNKNOWN),
        (True, CanonicalStatus.UNKNOWN),
        ("2", CanonicalStatus.UNKNOWN),
    ],
)
def test_status_mapping(source_status: object, canonical_status: CanonicalStatus) -> None:
    transaction = map_document(valid_document(Status=source_status))
    assert transaction.status is canonical_status


@pytest.mark.parametrize(
    "overrides",
    [
        {"AmountAsDecimal": "100.1"},
        {"AmountAsDecimal": "100", "Amount": "101"},
        {"AmountAsDecimal": "not-a-number"},
        {"Currency": "PHP"},
    ],
)
def test_invalid_amount_or_tenant_currency_is_visible(
    overrides: dict[str, object],
) -> None:
    transaction = map_document(valid_document(**overrides))
    assert transaction.amount_status is AmountStatus.INVALID
    assert transaction.amount_minor is None


def test_missing_currency_is_visible() -> None:
    transaction = map_document(valid_document(Currency=None))
    assert transaction.amount_status is AmountStatus.NO_CURRENCY
    assert transaction.amount_minor is None


def test_non_monetary_type_does_not_require_amount() -> None:
    transaction = map_document(
        valid_document(Type=9, Currency=None, AmountAsDecimal=None, Amount=None)
    )
    assert transaction.txn_type is TxnType.CARD_LINK
    assert transaction.amount_status is AmountStatus.NOT_APPLICABLE
    assert transaction.is_money_movement is False
    assert transaction.is_reconcilable is False


def test_refund_does_not_reuse_legacy_external_transaction_id() -> None:
    transaction = map_document(
        valid_document(Type=8, RefundFor="parent-1", ExternalTxnId="parent-provider-id")
    )
    assert transaction.txn_type is TxnType.REFUND
    assert transaction.refund_of == "parent-1"
    assert transaction.provider_txn_id is None
    assert transaction.provider_ref_id == "r1"


def test_synthetic_flag_requires_verified_configuration() -> None:
    transaction = map_document(valid_document(isSyntheticTestData="synthetic"))
    assert transaction.is_test_data is True

    with pytest.raises(ValueError, match="Unverified Asia synthetic-data flag"):
        map_document(valid_document(isSyntheticTestData="unknown"))


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="Naive datetimes"):
        map_document(valid_document(CreatedAt=datetime(2026, 9, 21)))


def test_timestamp_is_converted_to_utc() -> None:
    local_timezone = timezone(timedelta(hours=7))
    transaction = map_document(
        valid_document(CreatedAt=datetime(2026, 9, 21, 8, tzinfo=local_timezone))
    )
    assert transaction.created_at == datetime(2026, 9, 21, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({"CreatedAt": datetime(2026, 9, 21, tzinfo=UTC)}, "missing _id"),
        ({"_id": "a1"}, "missing CreatedAt"),
    ],
)
def test_required_source_fields(document: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        map_document(document)


def test_unconfigured_tenant_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unconfigured Asia tenant_id"):
        map_document(valid_document(), tenant_id="not-configured")


def test_bill_ids_must_be_a_collection() -> None:
    with pytest.raises(ValueError, match="BillIds must be a list"):
        map_document(valid_document(BillIds="bill1"))
