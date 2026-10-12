from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from bootstrap.health_evaluation import _scope, run_evaluation_cycle
from monitoring.adapters.health_config import JsonRuleConfigRepository
from monitoring.domain import AlertAction, HealthStatus

RULES = Path(__file__).parents[2] / "config" / "health-rules.demo.json"
NOW = datetime(2026, 10, 5, 10, 16, tzinfo=UTC)


class RecordingStatusRepository:
    def __init__(self) -> None:
        self.successes = []
        self.failures = []

    def record_success(self, scope, **values) -> None:
        self.successes.append((scope, values))

    def record_failure(self, scope, **values) -> None:
        self.failures.append((scope, values))


def _output(scope, *, replayed=False):
    evaluation = SimpleNamespace(
        scope=scope,
        status=HealthStatus.UNHEALTHY,
        evaluations=(1, 2, 3),
        data_as_of=NOW,
    )
    transitions = (
        SimpleNamespace(action=AlertAction.OPEN),
        SimpleNamespace(action=AlertAction.UPDATE),
        SimpleNamespace(action=AlertAction.RESOLVE),
    )
    return SimpleNamespace(
        evaluation=evaluation,
        transitions=() if replayed else transitions,
        notification_failures=0,
        replayed=replayed,
    )


def test_runner_summary_counts_transitions_replay_and_sanitized_failure() -> None:
    first = _scope("VN::ONEPAY")
    replay = _scope("INDIA::RAZORPAY")
    failed = _scope("ASIA:asia-id:ESPAY")
    statuses = RecordingStatusRepository()

    def evaluate(scope, as_of):
        assert as_of == NOW
        if scope == failed:
            raise RuntimeError("database unavailable")
        return _output(scope, replayed=scope == replay)

    summary = run_evaluation_cycle(
        (first, replay, failed),
        as_of=NOW,
        freshness_sla_seconds=600,
        evaluate=evaluate,
        status_repository=statuses,
    )

    assert summary["status"] == "FAILED"
    assert summary["evaluated_scopes"] == 2
    assert summary["evaluated_rules"] == 6
    assert (summary["opened"], summary["updated"], summary["resolved"]) == (1, 1, 1)
    assert summary["skipped"] == 1
    assert summary["failures"] == [
        {
            "region": "ASIA",
            "tenant_scope": "asia-id",
            "provider": "ESPAY",
            "error_code": "RuntimeError",
            "error_message": "database unavailable",
        }
    ]
    assert len(statuses.successes) == 2
    assert len(statuses.failures) == 1


def test_scope_parser_and_versioned_demo_config() -> None:
    scope = _scope("asia:asia-ph:2c2p")
    assert (scope.region.value, scope.tenant_scope, scope.provider) == (
        "ASIA",
        "asia-ph",
        "2C2P",
    )
    rules = JsonRuleConfigRepository(RULES).list_active()
    assert len(rules) == 3
    assert {rule.rule_id for rule in rules} == {
        "DEMO_LOW_SUCCESS_RATE_V1",
        "DEMO_HIGH_FAILURE_RATE_V1",
        "DEMO_STALE_DATA_V1",
    }

    with pytest.raises(ValueError, match="rule profile"):
        JsonRuleConfigRepository(RULES, required_profile="PRODUCTION").list_active()
