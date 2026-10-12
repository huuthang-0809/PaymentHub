import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from canonical.domain import CanonicalTransaction
from monitoring.domain import CanonicalEvent, Window, WindowAggregator
from shared_kernel import AmountStatus, CanonicalStatus, Region, TxnType
from shared_kernel.enums import Instrument


FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "monitoring"
COMPUTED_AT = datetime(2026, 9, 25, 11, 0, tzinfo=UTC)


def _canonical_event(values: dict[str, object]) -> CanonicalEvent:
    transaction = CanonicalTransaction(
        region=Region(values["region"]),
        tenant_id=values["tenant_id"],
        txn_id=values["txn_id"],
        txn_type=TxnType(values["txn_type"]),
        instrument=Instrument(values["instrument"]),
        channel_raw=values["channel_raw"],
        provider=values["provider"],
        status=CanonicalStatus(values["status"]),
        amount_status=AmountStatus(values["amount_status"]),
        amount_minor=values["amount_minor"],
        currency=values["currency"],
        created_at=datetime.fromisoformat(values["created_at"]),
        updated_at=(
            datetime.fromisoformat(values["updated_at"])
            if values["updated_at"] is not None
            else None
        ),
        is_terminal=values["is_terminal"],
        is_money_movement=values["is_money_movement"],
        is_reconcilable=values["is_reconcilable"],
        is_deleted=values["is_deleted"],
        is_test_data=values["is_test_data"],
    )
    return CanonicalEvent(
        str(values["event_id"]),
        transaction,
        parse_error=bool(values.get("parse_error", False)),
    )


def _events() -> list[CanonicalEvent]:
    return [
        _canonical_event(json.loads(line))
        for line in (FIXTURE_DIR / "events.jsonl").read_text().splitlines()
        if line.strip()
    ]


def _expected_rows(size: int) -> list[dict[str, object]]:
    document = json.loads((FIXTURE_DIR / f"expected-{size}m.json").read_text())
    return document["rows"]


def _dimensions(aggregate) -> dict[str, object]:
    key = aggregate.key
    return {
        "window_start": key.window.start.isoformat().replace("+00:00", "Z"),
        "window_end": key.window.end.isoformat().replace("+00:00", "Z"),
        "region": key.region.value,
        "tenant_scope": key.tenant_scope,
        "provider": key.provider,
        "txn_type": key.txn_type.value,
        "instrument": key.instrument,
        "channel_raw": key.channel_raw,
        "currency": key.currency,
    }


def _metrics(aggregate) -> dict[str, object]:
    def rate(value: Decimal | None) -> str | None:
        return str(value) if value is not None else None

    return {
        "total_event_count": aggregate.total_event_count,
        "operation_event_count": aggregate.operation_event_count,
        "success_count": aggregate.success_count,
        "failed_count": aggregate.failed_count,
        "pending_count": aggregate.pending_count,
        "unknown_count": aggregate.unknown_count,
        "success_rate": rate(aggregate.success_rate),
        "failed_rate": rate(aggregate.failed_rate),
        "unknown_rate": rate(aggregate.unknown_rate),
        "monetary_count": aggregate.monetary_count,
        "monetary_amount_minor": aggregate.monetary_amount_minor,
        "invalid_amount_count": aggregate.invalid_amount_count,
        "missing_currency_count": aggregate.missing_currency_count,
        "parse_error_count": aggregate.parse_error_count,
        "late_event_count": aggregate.late_event_count,
        "non_terminal_count": aggregate.non_terminal_count,
        "pending_age_lt_5m_count": aggregate.pending_age_lt_5m_count,
        "pending_age_5_to_15m_count": aggregate.pending_age_5_to_15m_count,
        "pending_age_15_to_60m_count": aggregate.pending_age_15_to_60m_count,
        "pending_age_gte_60m_count": aggregate.pending_age_gte_60m_count,
        "oldest_pending_age_seconds": aggregate.oldest_pending_age_seconds,
        "freshness_lag_seconds": aggregate.freshness_lag_seconds,
    }


def _row_key(dimensions: dict[str, object]) -> str:
    return json.dumps(dimensions, sort_keys=True)


@pytest.mark.parametrize("size", [1, 5, 15])
def test_window_aggregation_matches_handwritten_golden_fixture(size: int) -> None:
    actual = WindowAggregator((size,)).aggregate(_events(), computed_at=COMPUTED_AT)
    actual_by_key = {
        _row_key(_dimensions(row)): _metrics(row)
        for row in actual
    }
    expected_by_key = {
        _row_key(row["dimensions"]): row["metrics"]
        for row in _expected_rows(size)
    }

    assert actual_by_key == expected_by_key
    assert all(row.computed_at == COMPUTED_AT for row in actual)



def test_latest_state_pending_metrics_exclude_resolved_transaction() -> None:
    rows = WindowAggregator((5,)).aggregate(_events(), computed_at=COMPUTED_AT)
    primary = next(
        row for row in rows
        if row.key.window.start == datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
        and row.key.region is Region.VN
        and row.key.txn_type is TxnType.PAYMENT
        and row.key.currency == "VND"
    )

    assert primary.pending_count == 3
    assert primary.non_terminal_count == 3
    assert primary.pending_age_15_to_60m_count == 2
    assert primary.oldest_pending_age_seconds == 3535


def test_window_is_utc_aware_and_half_open() -> None:
    window = Window.containing(datetime(2026, 9, 25, 10, 4, 59, tzinfo=UTC), 5)

    assert window.start == datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
    assert window.end == datetime(2026, 9, 25, 10, 5, tzinfo=UTC)
    assert window.contains(datetime(2026, 9, 25, 10, 4, 59, 999999, tzinfo=UTC))
    assert not window.contains(datetime(2026, 9, 25, 10, 5, tzinfo=UTC))
    assert Window.containing(window.end, 5).start == window.end


@pytest.mark.parametrize("size", [1, 5, 15])
def test_supported_window_sizes(size: int) -> None:
    event_time = datetime(2026, 9, 25, 10, 14, 30, tzinfo=UTC)
    window = Window.containing(event_time, size)

    assert window.size_minutes == size
    assert window.contains(event_time)


def test_created_at_fallback_and_last_event_at_are_deterministic() -> None:
    rows = WindowAggregator().aggregate(_events(), computed_at=COMPUTED_AT)
    second_window = next(
        row
        for row in rows
        if row.key.window.start == datetime(2026, 9, 25, 10, 5, tzinfo=UTC)
    )

    assert second_window.last_event_at == datetime(2026, 9, 25, 10, 6, tzinfo=UTC)


def test_duplicate_identity_with_different_payload_is_rejected() -> None:
    first = _events()[0]
    collision = CanonicalEvent(
        first.event_id,
        replace(first.transaction, status=CanonicalStatus.FAILED),
    )

    with pytest.raises(ValueError, match="event_id collision"):
        WindowAggregator().aggregate([first, collision], computed_at=COMPUTED_AT)


def test_naive_computed_at_and_unsupported_window_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        WindowAggregator().aggregate([], computed_at=datetime(2026, 9, 25))
    with pytest.raises(ValueError, match="1, 5 or 15"):
        Window.containing(COMPUTED_AT, 3)
