from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from monitoring.domain import (
    Comparison,
    EvaluationScope,
    EvaluationStatus,
    HealthEvaluator,
    HealthImpact,
    HealthMetric,
    HealthStatus,
    HealthWindow,
    MetricRule,
    RuleConfig,
    Severity,
)
from shared_kernel import Region


FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "monitoring" / "health-alerts"


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _scope(source: dict, name: str) -> EvaluationScope:
    item = source["scopes"][name]
    return EvaluationScope(
        region=Region(item["region"]),
        tenant_scope=item["tenant_scope"],
        provider=item["provider"],
    )


def _configs() -> tuple[RuleConfig, ...]:
    source = _load("demo-rule-config.json")
    return tuple(
        RuleConfig(
            rule_id=item["rule_id"],
            rule_version=item["rule_version"],
            metric=HealthMetric(item["metric"]),
            comparison=Comparison(item["comparison"]),
            threshold=Decimal(str(item["threshold"])),
            minimum_sample=item["minimum_sample"],
            window_size_minutes=item["window_size_minutes"],
            lookback_windows=item["lookback_windows"],
            breach_windows=item["breach_windows"],
            recovery_windows=item["recovery_windows"],
            health_impact=HealthImpact(item["health_impact"]),
            severity=Severity(item["severity"]),
            cooldown_seconds=item["cooldown_seconds"],
            runbook_ref=item["runbook_ref"],
        )
        for item in source["rules"]
    )


def _evaluator() -> HealthEvaluator:
    return HealthEvaluator(MetricRule(config) for config in _configs())


def _windows(source: dict, cycle: dict, scope_name: str) -> tuple[HealthWindow, ...]:
    scope = _scope(source, scope_name)
    return tuple(
        HealthWindow(
            scope=scope,
            window_size_minutes=source["window_size_minutes"],
            window_start=_timestamp(item["window_start"]),
            window_end=_timestamp(item["window_end"]),
            operation_event_count=item["operation_event_count"],
            success_count=item["success_count"],
            failed_count=item["failed_count"],
            data_as_of=_timestamp(item["data_as_of"]),
        )
        for item in cycle["windows"]
        if item["scope"] == scope_name
    )


def test_health_evaluator_matches_handwritten_onepay_timeline() -> None:
    source = _load("window-series.json")
    expected = _load("expected-health-alerts.json")
    expected_by_cycle = {item["cycle_id"]: item for item in expected["onepay-vn"]}
    scope = _scope(source, "onepay-vn")
    evaluator = _evaluator()
    states = ()

    results = {}
    for cycle in source["cycles"]:
        result = evaluator.evaluate(
            scope,
            _windows(source, cycle, "onepay-vn"),
            prior_states=states,
            evaluated_at=_timestamp(cycle["evaluated_at"]),
        )
        states = result.next_states
        results[cycle["cycle_id"]] = result
        oracle = expected_by_cycle[cycle["cycle_id"]]

        assert result.status.value == oracle["health_status"]
        assert [item.status.value for item in result.evaluations[:2]] == oracle[
            "rate_rule_statuses"
        ]
        assert str(result.evaluations[0].reason.observed_value) == oracle[
            "success_rate"
        ]
        assert str(result.evaluations[1].reason.observed_value) == oracle[
            "failure_rate"
        ]
        assert result.evaluations[2].status is EvaluationStatus.PASS
        assert result.evaluations[2].reason.observed_value == Decimal(70)

    assert results["c2-first-breach"].evaluations[0].breach_confirmed is False
    assert results["c3-open"].evaluations[0].breach_confirmed is True
    assert results["c3-open"].evaluations[1].breach_confirmed is True
    assert results["c4-continued-breach"].evaluations[0].consecutive_breaches == 3
    assert results["c5-first-recovery"].evaluations[0].recovery_confirmed is False
    assert results["c6-resolve"].evaluations[0].recovery_confirmed is True
    assert results["c6-resolve"].evaluations[1].recovery_confirmed is True


def test_small_sample_and_no_data_match_handwritten_oracle() -> None:
    source = _load("window-series.json")
    expected = _load("expected-health-alerts.json")
    evaluator = _evaluator()

    stripe_scope = _scope(source, "stripe-asia-id")
    for cycle in source["cycles"]:
        result = evaluator.evaluate(
            stripe_scope,
            _windows(source, cycle, "stripe-asia-id"),
            evaluated_at=_timestamp(cycle["evaluated_at"]),
        )
        assert result.status.value == expected["stripe-asia-id"][
            "all_cycles_health_status"
        ]
        assert [item.status.value for item in result.evaluations[:2]] == expected[
            "stripe-asia-id"
        ]["rate_rule_statuses"]
        assert result.evaluations[2].status.value == expected["stripe-asia-id"][
            "freshness_rule_status"
        ]

    no_data = source["edge_cases"][0]
    result = evaluator.evaluate(
        _scope(source, no_data["scope"]),
        (),
        data_as_of=no_data["data_as_of"],
        evaluated_at=_timestamp(no_data["evaluated_at"]),
    )
    assert result.status.value == expected["razorpay-india"]["health_status"]
    assert [item.status.value for item in result.evaluations] == expected[
        "razorpay-india"
    ]["rule_statuses"]


def test_multi_reason_precedence_preserves_both_breaches() -> None:
    source = _load("window-series.json")
    cycle = source["cycles"][2]
    scope = _scope(source, "onepay-vn")

    result = _evaluator().evaluate(
        scope,
        _windows(source, cycle, "onepay-vn"),
        evaluated_at=_timestamp(cycle["evaluated_at"]),
    )

    assert result.status is HealthStatus.UNHEALTHY
    assert {reason.health_impact for reason in result.reasons} == {
        HealthImpact.UNHEALTHY,
        HealthImpact.DEGRADED,
    }



def test_stale_data_rule_runs_without_window_rows() -> None:
    source = _load("window-series.json")
    evaluated_at = datetime(2026, 10, 5, 10, 31, tzinfo=UTC)

    result = _evaluator().evaluate(
        _scope(source, "razorpay-india"),
        (),
        data_as_of=evaluated_at - timedelta(seconds=601),
        evaluated_at=evaluated_at,
    )

    freshness = result.evaluations[2]
    assert result.status is HealthStatus.DEGRADED
    assert freshness.status is EvaluationStatus.BREACH
    assert freshness.reason.reason_code == "STALE_DATA"
    assert freshness.reason.observed_value == Decimal(601)
    assert freshness.breach_confirmed is True

@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"minimum_sample": 0}, "minimum_sample"),
        ({"threshold": Decimal("1.1")}, "threshold"),
        ({"window_size_minutes": 7}, "window size"),
        ({"breach_windows": 0}, "breach/recovery"),
        ({"cooldown_seconds": -1}, "cooldown"),
    ],
)
def test_invalid_rate_rule_config_is_rejected(changes: dict, message: str) -> None:
    values = {
        "rule_id": "DEMO_RULE",
        "rule_version": 1,
        "metric": HealthMetric.SUCCESS_RATE,
        "comparison": Comparison.LT,
        "threshold": Decimal("0.8"),
        "minimum_sample": 100,
        "window_size_minutes": 5,
        "lookback_windows": 1,
        "breach_windows": 2,
        "recovery_windows": 2,
        "health_impact": HealthImpact.UNHEALTHY,
        "severity": Severity.CRITICAL,
        "cooldown_seconds": 900,
        "runbook_ref": "fixture://rule",
    }
    values.update(changes)

    with pytest.raises(ValueError, match=message):
        RuleConfig(**values)


def test_evaluator_rejects_open_window_and_wrong_scope() -> None:
    source = _load("window-series.json")
    cycle = source["cycles"][0]
    onepay = _scope(source, "onepay-vn")
    stripe = _scope(source, "stripe-asia-id")
    window = _windows(source, cycle, "onepay-vn")[0]
    evaluator = _evaluator()

    with pytest.raises(ValueError, match="open window"):
        evaluator.evaluate(
            onepay,
            (window,),
            evaluated_at=window.window_end - timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="match evaluation scope"):
        evaluator.evaluate(
            stripe,
            (window,),
            evaluated_at=_timestamp(cycle["evaluated_at"]),
        )


def test_injected_clock_makes_evaluation_deterministic() -> None:
    source = _load("window-series.json")
    cycle = source["cycles"][0]
    now = _timestamp(cycle["evaluated_at"])
    scope = _scope(source, "onepay-vn")
    evaluator = HealthEvaluator(
        (MetricRule(config) for config in _configs()),
        clock=lambda: now,
    )

    result = evaluator.evaluate(scope, _windows(source, cycle, "onepay-vn"))

    assert result.evaluated_at == now
    assert result.evaluated_at.tzinfo is UTC
