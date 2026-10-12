import json

import pytest
from collections import Counter
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from canonical.domain import CanonicalTransaction
from shared_kernel import AmountStatus, CanonicalStatus, Region, TxnType
from shared_kernel.enums import Instrument

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "monitoring"


def _events() -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (FIXTURE_DIR / "events.jsonl").read_text().splitlines()
        if line.strip()
    ]


def _expected(size: int = 5) -> dict[str, object]:
    return json.loads((FIXTURE_DIR / f"expected-{size}m.json").read_text())


def test_monitoring_fixture_contains_required_t1_cases() -> None:
    events = _events()
    scenarios = {event["scenario"] for event in events}
    identifiers = Counter(event["event_id"] for event in events)

    assert len(events) == 25
    assert len(identifiers) == 24
    assert {key for key, count in identifiers.items() if count > 1} == {"e01"}
    assert max(identifiers.values()) == 2
    assert {"success-100", "failed", "pending", "unknown"} <= scenarios
    assert {"deleted-success", "test-data-success"} <= scenarios
    assert {"card-link", "tokenization"} <= scenarios
    assert {"invalid-amount-success", "missing-currency"} <= scenarios
    parse_errors = [event for event in events if event.get("parse_error")]
    assert [event["event_id"] for event in parse_errors] == ["e06"]
    assert {"boundary-100459", "boundary-100500"} <= scenarios
    assert "created-at-fallback" in scenarios

    transitions = [event for event in events if event["txn_id"] == "vn-transition"]
    assert {event["status"] for event in transitions} == {"PENDING", "SUCCESS"}

    isolated = [event for event in events if event["txn_id"] == "shared-txn"]
    assert {event["tenant_id"] for event in isolated} == {"asia-id", "asia-ph"}
    assert {event["currency"] for event in isolated} == {"IDR", "PHP"}


def test_fixture_records_are_valid_canonical_transactions() -> None:
    canonical = []
    for event in _events():
        canonical.append(
            CanonicalTransaction(
                region=Region(event["region"]),
                tenant_id=event["tenant_id"],
                txn_id=event["txn_id"],
                txn_type=TxnType(event["txn_type"]),
                instrument=Instrument(event["instrument"]),
                channel_raw=event["channel_raw"],
                provider=event["provider"],
                status=CanonicalStatus(event["status"]),
                amount_status=AmountStatus(event["amount_status"]),
                amount_minor=event["amount_minor"],
                currency=event["currency"],
                created_at=datetime.fromisoformat(event["created_at"]),
                updated_at=(
                    datetime.fromisoformat(event["updated_at"])
                    if event["updated_at"] is not None
                    else None
                ),
                is_terminal=event["is_terminal"],
                is_money_movement=event["is_money_movement"],
                is_reconcilable=event["is_reconcilable"],
                is_deleted=event["is_deleted"],
                is_test_data=event["is_test_data"],
            )
        )

    assert len(canonical) == 25


def test_monitoring_fixture_times_are_utc_and_cover_window_boundary() -> None:
    by_scenario = {event["scenario"]: event for event in _events()}
    timestamps = []
    for event in _events():
        timestamps.append(datetime.fromisoformat(str(event["created_at"])))
        if event["updated_at"] is not None:
            timestamps.append(datetime.fromisoformat(str(event["updated_at"])))

    assert all(value.utcoffset() is not None for value in timestamps)
    assert by_scenario["boundary-100459"]["updated_at"] == "2026-09-25T10:04:59Z"
    assert by_scenario["boundary-100500"]["updated_at"] == "2026-09-25T10:05:00Z"
    fallback = by_scenario["created-at-fallback"]
    assert fallback["updated_at"] is None
    assert fallback["created_at"] == "2026-09-25T10:06:00Z"


@pytest.mark.parametrize("size", [1, 5, 15])
def test_handwritten_expected_is_internally_consistent(size: int) -> None:
    expected = _expected(size)
    rows = expected["rows"]

    assert expected["input_records"] == 25
    assert expected["unique_events"] == 24
    assert expected["base_event_count"] == 22
    assert len(rows) == {1: 15, 5: 10, 15: 9}[size]
    assert sum(row["metrics"]["total_event_count"] for row in rows) == 22

    dimension_keys = [
        tuple(row["dimensions"].items())
        for row in rows
    ]
    assert len(dimension_keys) == len(set(dimension_keys))

    quantum = Decimal("0.000001")
    for row in rows:
        metrics = row["metrics"]
        operation_count = metrics["operation_event_count"]
        bucket_total = sum(
            metrics[name]
            for name in (
                "success_count",
                "failed_count",
                "pending_count",
                "unknown_count",
            )
        )
        assert bucket_total == operation_count
        pending_age_total = sum(
            metrics[name]
            for name in (
                "pending_age_lt_5m_count",
                "pending_age_5_to_15m_count",
                "pending_age_15_to_60m_count",
                "pending_age_gte_60m_count",
            )
        )
        assert pending_age_total <= metrics["non_terminal_count"]

        for count_name, rate_name in (
            ("success_count", "success_rate"),
            ("failed_count", "failed_rate"),
            ("unknown_count", "unknown_rate"),
        ):
            if operation_count == 0:
                assert metrics[rate_name] is None
            else:
                calculated = (
                    Decimal(metrics[count_name]) / Decimal(operation_count)
                ).quantize(quantum, rounding=ROUND_HALF_UP)
                assert Decimal(metrics[rate_name]) == calculated


def test_primary_handwritten_row_matches_readme_arithmetic() -> None:
    expected = _expected()
    primary = next(
        row
        for row in expected["rows"]
        if row["dimensions"] == {
            "window_start": "2026-09-25T10:00:00Z",
            "window_end": "2026-09-25T10:05:00Z",
            "region": "VN",
            "tenant_scope": "",
            "provider": "ONEPAY",
            "txn_type": "PAYMENT",
            "instrument": "CARD",
            "channel_raw": "CARD",
            "currency": "VND",
        }
    )

    assert primary["metrics"] == {
        "total_event_count": 12,
        "operation_event_count": 12,
        "success_count": 5,
        "failed_count": 3,
        "pending_count": 3,
        "unknown_count": 1,
        "success_rate": "0.416667",
        "failed_rate": "0.250000",
        "unknown_rate": "0.083333",
        "monetary_count": 4,
        "monetary_amount_minor": 750,
        "invalid_amount_count": 1,
        "missing_currency_count": 0,
        "parse_error_count": 1,
        "late_event_count": 0,
        "non_terminal_count": 3,
        "pending_age_lt_5m_count": 0,
        "pending_age_5_to_15m_count": 0,
        "pending_age_15_to_60m_count": 2,
        "pending_age_gte_60m_count": 0,
        "oldest_pending_age_seconds": 3535,
        "freshness_lag_seconds": 3301,
    }
