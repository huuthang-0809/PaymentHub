from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "monitoring" / "health-alerts"
QUANTUM = Decimal("0.000001")


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _rate(numerator: int, denominator: int) -> str:
    return str(
        (Decimal(numerator) / Decimal(denominator)).quantize(
            QUANTUM, rounding=ROUND_HALF_UP
        )
    )


def test_health_alert_fixture_has_required_w4_cases() -> None:
    source = _load("window-series.json")
    expected = _load("expected-health-alerts.json")

    assert [cycle["cycle_id"] for cycle in source["cycles"]] == [
        "c1-normal",
        "c2-first-breach",
        "c3-open",
        "c4-continued-breach",
        "c5-first-recovery",
        "c6-resolve",
    ]
    assert set(source["scopes"]) == {
        "onepay-vn",
        "stripe-asia-id",
        "razorpay-india",
    }
    assert {scope["provider"] for scope in source["scopes"].values()} == {
        "ONEPAY",
        "STRIPE",
        "RAZORPAY",
    }
    assert source["scopes"]["stripe-asia-id"]["tenant_scope"] == "asia-id"
    assert expected["razorpay-india"]["health_status"] == "NO_DATA"


def test_demo_rule_config_is_explicit_and_never_claims_production() -> None:
    config = _load("demo-rule-config.json")

    assert config["profile"] == "DEMO_ONLY"
    assert len(config["rules"]) == 3
    assert all(rule["rule_id"].startswith("DEMO_") for rule in config["rules"])
    for rule in config["rules"]:
        assert rule["rule_version"] == 1
        assert rule["comparison"] in {"LT", "GTE", "GT"}
        assert rule["threshold"] is not None
        assert rule["breach_windows"] >= 1
        assert rule["recovery_windows"] >= 1
        assert rule["cooldown_seconds"] >= 0
        assert rule["runbook_ref"].startswith("fixture://")


def test_windows_are_closed_utc_five_minute_rows_with_consistent_counts() -> None:
    source = _load("window-series.json")

    for cycle in source["cycles"]:
        evaluated_at = _timestamp(cycle["evaluated_at"])
        assert evaluated_at.tzinfo is UTC
        assert {row["scope"] for row in cycle["windows"]} == {
            "onepay-vn",
            "stripe-asia-id",
        }
        for row in cycle["windows"]:
            start = _timestamp(row["window_start"])
            end = _timestamp(row["window_end"])
            data_as_of = _timestamp(row["data_as_of"])
            assert start.tzinfo is UTC and end.tzinfo is UTC
            assert (end - start).total_seconds() == 300
            assert end <= evaluated_at
            assert start <= data_as_of < end
            assert row["success_count"] + row["failed_count"] == row[
                "operation_event_count"
            ]


def test_handwritten_onepay_rates_and_health_are_internally_consistent() -> None:
    source = _load("window-series.json")
    expected = _load("expected-health-alerts.json")
    expected_by_cycle = {
        row["cycle_id"]: row for row in expected["onepay-vn"]
    }

    for cycle in source["cycles"]:
        row = next(item for item in cycle["windows"] if item["scope"] == "onepay-vn")
        oracle = expected_by_cycle[cycle["cycle_id"]]
        assert oracle["success_rate"] == _rate(
            row["success_count"], row["operation_event_count"]
        )
        assert oracle["failure_rate"] == _rate(
            row["failed_count"], row["operation_event_count"]
        )

        low_breach = Decimal(oracle["success_rate"]) < Decimal("0.800000")
        high_breach = Decimal(oracle["failure_rate"]) >= Decimal("0.200000")
        expected_statuses = [
            "BREACH" if low_breach else "PASS",
            "BREACH" if high_breach else "PASS",
        ]
        assert oracle["rate_rule_statuses"] == expected_statuses
        assert oracle["health_status"] == (
            "UNHEALTHY" if low_breach else "HEALTHY"
        )


def test_handwritten_alert_lifecycle_dedup_cooldown_and_recovery() -> None:
    expected = _load("expected-health-alerts.json")
    cycles = {row["cycle_id"]: row for row in expected["onepay-vn"]}

    assert cycles["c1-normal"]["alert_actions"] == []
    assert cycles["c2-first-breach"]["alert_actions"] == []

    opened = cycles["c3-open"]["alert_actions"]
    updated = cycles["c4-continued-breach"]["alert_actions"]
    resolved = cycles["c6-resolve"]["alert_actions"]
    assert len(opened) == len(updated) == len(resolved) == 2
    assert {item["rule_id"] for item in opened} == {
        "DEMO_LOW_SUCCESS_RATE_V1",
        "DEMO_HIGH_FAILURE_RATE_V1",
    }
    assert all(item["action"] == "OPEN" for item in opened)
    assert all(item["occurrence_count"] == 1 for item in opened)
    assert all(item["notification_due"] is True for item in opened)
    assert all(item["action"] == "UPDATE" for item in updated)
    assert all(item["occurrence_count"] == 2 for item in updated)
    assert all(item["notification_due"] is False for item in updated)
    assert cycles["c5-first-recovery"]["alert_actions"] == []
    assert all(item["action"] == "RESOLVE" for item in resolved)
    assert all(item["status"] == "RESOLVED" for item in resolved)
    assert all(item["occurrence_count"] == 2 for item in resolved)


def test_small_sample_no_data_and_multi_reason_precedence_are_explicit() -> None:
    expected = _load("expected-health-alerts.json")

    small = expected["stripe-asia-id"]
    assert small["operation_event_count_each_cycle"] < 100
    assert small["rate_rule_statuses"] == [
        "INSUFFICIENT_DATA",
        "INSUFFICIENT_DATA",
    ]
    assert small["freshness_rule_status"] == "PASS"
    assert small["all_cycles_health_status"] == "HEALTHY"
    assert small["alert_actions"] == []

    no_data = expected["razorpay-india"]
    assert no_data["rule_statuses"] == ["INSUFFICIENT_DATA"] * 3
    assert no_data["health_status"] == "NO_DATA"
    assert no_data["alert_actions"] == []

    precedence = expected["precedence_proof"]
    assert {reason["health_impact"] for reason in precedence["reasons"]} == {
        "DEGRADED",
        "UNHEALTHY",
    }
    assert precedence["result"] == "UNHEALTHY"
