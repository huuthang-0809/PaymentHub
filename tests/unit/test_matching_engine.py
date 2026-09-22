from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from canonical.domain import CanonicalTransaction
from reconciliation.domain.models import ProviderTransaction, ReconResultType
from reconciliation.domain.services import MatchingEngine
from shared_kernel import AmountStatus, CanonicalStatus, Region, TxnType


NOW = datetime(2026, 9, 22, 8, tzinfo=UTC)


def system_transaction(**overrides: object) -> CanonicalTransaction:
    values = {
        "region": Region.VN,
        "tenant_id": None,
        "txn_id": "system-1",
        "txn_type": TxnType.PAYMENT,
        "provider": "ONEPAY",
        "status": CanonicalStatus.SUCCESS,
        "amount_status": AmountStatus.OK,
        "created_at": NOW,
        "is_terminal": True,
        "is_money_movement": True,
        "is_reconcilable": True,
        "provider_txn_id": "provider-1",
        "provider_ref_id": "reference-1",
        "merchant_ref": "merchant-reference-1",
        "merchant_id": "merchant-1",
        "amount_minor": 10_000,
        "currency": "VND",
    }
    values.update(overrides)
    return CanonicalTransaction(**values)  # type: ignore[arg-type]


def provider_transaction(**overrides: object) -> ProviderTransaction:
    values = {
        "provider": "ONEPAY",
        "region": Region.VN,
        "tenant_id": None,
        "provider_txn_id": "provider-1",
        "provider_ref_id": "reference-1",
        "merchant_ref": "merchant-reference-1",
        "merchant_id": "merchant-1",
        "amount_minor": 10_000,
        "currency": "VND",
        "status": "SUCCESS",
        "txn_type": TxnType.PAYMENT,
        "transaction_time": NOW,
        "source_file": "statement.csv",
        "source_row_number": 1,
    }
    values.update(overrides)
    return ProviderTransaction(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("system_overrides", "provider_overrides"),
    [
        ({}, {}),
        (
            {
                "region": Region.ASIA,
                "tenant_id": "asia-id",
                "provider": "ESPAY",
                "currency": "IDR",
                "provider_txn_id": "system-provider-id",
            },
            {
                "region": Region.ASIA,
                "tenant_id": "asia-id",
                "provider": "ESPAY",
                "currency": "IDR",
                "provider_txn_id": "statement-provider-id",
            },
        ),
        (
            {
                "provider_txn_id": "system-provider-id",
                "provider_ref_id": "system-reference-id",
            },
            {
                "provider_txn_id": "statement-provider-id",
                "provider_ref_id": "statement-reference-id",
            },
        ),
    ],
)
def test_matches_by_each_exact_identifier(
    system_overrides: dict[str, object],
    provider_overrides: dict[str, object],
) -> None:
    results = MatchingEngine().match(
        "run-1",
        [provider_transaction(**provider_overrides)],
        [system_transaction(**system_overrides)],
    )

    assert len(results) == 1
    assert results[0].result_type is ReconResultType.MATCHED
    assert results[0].system_txn_id == "system-1"
    assert results[0].delta_amount == 0


def test_exact_match_reports_amount_mismatch() -> None:
    result = MatchingEngine().match(
        "run-1",
        [provider_transaction(amount_minor=9_000)],
        [system_transaction()],
    )[0]

    assert result.result_type is ReconResultType.AMOUNT_MISMATCH
    assert result.delta_amount == 1_000


def test_provider_reference_is_only_used_for_asia() -> None:
    system = system_transaction(
        provider_txn_id=None,
        merchant_ref="system-ref",
        merchant_id="system-merchant",
    )
    provider = provider_transaction(
        provider_txn_id=None,
        merchant_ref="provider-ref",
        merchant_id="provider-merchant",
    )

    results = MatchingEngine().match("run-1", [provider], [system])

    assert {result.result_type for result in results} == {
        ReconResultType.MISSING_IN_PROVIDER,
        ReconResultType.MISSING_IN_SYSTEM,
    }