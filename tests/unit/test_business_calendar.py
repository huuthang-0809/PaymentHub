from dataclasses import replace
from datetime import UTC, date, datetime, time

import pytest

from reconciliation.application.calendar import (
    classify_provider_rows,
    resolve_business_period,
)
from reconciliation.domain import ProviderTransaction
from shared_kernel import Region, TxnType


def _row(transaction_time: datetime) -> ProviderTransaction:
    return ProviderTransaction(
        provider="ONEPAY",
        region=Region.VN,
        tenant_id=None,
        provider_txn_id="provider-1",
        provider_ref_id=None,
        merchant_ref=None,
        merchant_id=None,
        amount_minor=100,
        currency="VND",
        status="SUCCESS",
        txn_type=TxnType.PAYMENT,
        transaction_time=transaction_time,
        source_file="statement.csv",
        source_row_number=2,
    )


def test_business_date_is_the_period_ending_at_local_cutoff() -> None:
    period = resolve_business_period(
        date(2026, 10, 2), "Asia/Ho_Chi_Minh", time(2)
    )

    assert period.start_utc == datetime(2026, 9, 30, 19, tzinfo=UTC)
    assert period.end_utc == datetime(2026, 10, 1, 19, tzinfo=UTC)


def test_row_at_cutoff_is_moved_out_of_current_period() -> None:
    period = resolve_business_period(date(2026, 10, 2), "UTC", time(2))
    before = _row(datetime(2026, 10, 2, 1, 59, 59, tzinfo=UTC))
    at_cutoff = _row(datetime(2026, 10, 2, 2, tzinfo=UTC))

    eligible, persisted = classify_provider_rows([before, at_cutoff], period)

    assert eligible == [persisted[0]]
    assert persisted[0].late_arriving is False
    assert persisted[1].late_arriving is True


def test_unknown_timezone_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown business timezone"):
        resolve_business_period(date(2026, 10, 2), "Not/AZone", time(2))


def test_previous_period_row_is_excluded_without_being_marked_late() -> None:
    period = resolve_business_period(date(2026, 10, 2), "UTC", time(2))
    eligible, persisted = classify_provider_rows(
        [_row(datetime(2026, 10, 1, 1, tzinfo=UTC))], period
    )
    assert eligible == []
    assert persisted[0].late_arriving is False


def test_classification_resets_stale_late_flag_without_mutating_input() -> None:
    period = resolve_business_period(date(2026, 10, 2), "UTC", time(2))
    row = replace(
        _row(datetime(2026, 10, 2, 1, tzinfo=UTC)), late_arriving=True,
    )

    eligible, persisted = classify_provider_rows(iter([row]), period)

    assert eligible == persisted == [replace(row, late_arriving=False)]
    assert row.late_arriving is True
