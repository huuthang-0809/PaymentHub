from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from itertools import count
from pathlib import Path

from monitoring.adapters.memory import (
    InMemoryAlertUnitOfWork,
    InMemoryHealthMetricSource,
    InMemoryRuleConfigRepository,
    RecordingNotificationPort,
)
from monitoring.application import AlertManager, EvaluateProviderHealth
from monitoring.domain import (
    AlertAction,
    AlertStatus,
    Comparison,
    EvaluationScope,
    HealthImpact,
    HealthMetric,
    HealthWindow,
    RuleConfig,
    Severity,
)
from shared_kernel import Region

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "monitoring" / "health-alerts"


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _scope(source: dict, name: str = "onepay-vn") -> EvaluationScope:
    item = source["scopes"][name]
    return EvaluationScope(
        region=Region(item["region"]),
        tenant_scope=item["tenant_scope"],
        provider=item["provider"],
    )


def _configs() -> tuple[RuleConfig, ...]:
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
        for item in _load("demo-rule-config.json")["rules"]
    )


def _windows(source: dict, cycle: dict) -> tuple[HealthWindow, ...]:
    scope = _scope(source)
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
        if item["scope"] == "onepay-vn"
    )


def _service(
    metric_source: InMemoryHealthMetricSource,
    unit_of_work: InMemoryAlertUnitOfWork,
    notifications: RecordingNotificationPort,
) -> EvaluateProviderHealth:
    ids = count(1)
    return EvaluateProviderHealth(
        metric_source,
        InMemoryRuleConfigRepository(_configs()),
        unit_of_work,
        notifications,
        alert_manager=AlertManager(lambda: f"alert-{next(ids)}"),
    )


def test_alert_lifecycle_matches_handwritten_oracle_and_commits_before_notify() -> None:
    source = _load("window-series.json")
    expected = {
        item["cycle_id"]: item
        for item in _load("expected-health-alerts.json")["onepay-vn"]
    }
    scope = _scope(source)
    metric_source = InMemoryHealthMetricSource()
    unit_of_work = InMemoryAlertUnitOfWork()
    notifications = RecordingNotificationPort(
        commit_probe=lambda: unit_of_work._committed
    )
    service = _service(metric_source, unit_of_work, notifications)
    opened_ids: dict[str, str] = {}

    for cycle in source["cycles"]:
        metric_source.set_windows(scope, _windows(source, cycle))
        output = service.execute(
            scope, evaluated_at=_timestamp(cycle["evaluated_at"])
        )
        oracle = expected[cycle["cycle_id"]]["alert_actions"]

        assert [transition.action.value for transition in output.transitions] == [
            item["action"] for item in oracle
        ]
        assert [transition.occurrence_count for transition in output.transitions] == [
            item["occurrence_count"] for item in oracle
        ]
        assert [transition.notification_due for transition in output.transitions] == [
            item["notification_due"] for item in oracle
        ]

        for transition in output.transitions:
            alert = unit_of_work.alerts.get(transition.alert_id)
            assert alert.status.value == next(
                item["status"] for item in oracle if item["rule_id"] == alert.rule_id
            )
            if transition.action is AlertAction.OPEN:
                opened_ids[alert.rule_id] = alert.alert_id
            else:
                assert alert.alert_id == opened_ids[alert.rule_id]

    alerts = unit_of_work.alerts.list_alerts()
    assert len(alerts) == 2
    assert {alert.status for alert in alerts} == {AlertStatus.RESOLVED}
    assert {alert.occurrence_count for alert in alerts} == {2}
    assert unit_of_work.commits == 6
    assert [item[1].action for item in notifications.delivered] == [
        AlertAction.OPEN,
        AlertAction.OPEN,
        AlertAction.RESOLVE,
        AlertAction.RESOLVE,
    ]


def test_notification_failure_keeps_committed_alerts_and_retry_does_not_duplicate() -> None:
    source = _load("window-series.json")
    scope = _scope(source)
    metric_source = InMemoryHealthMetricSource()
    unit_of_work = InMemoryAlertUnitOfWork()
    notifications = RecordingNotificationPort(
        commit_probe=lambda: unit_of_work._committed
    )
    service = _service(metric_source, unit_of_work, notifications)

    for cycle in source["cycles"][:2]:
        metric_source.set_windows(scope, _windows(source, cycle))
        service.execute(scope, evaluated_at=_timestamp(cycle["evaluated_at"]))

    open_cycle = source["cycles"][2]
    metric_source.set_windows(scope, _windows(source, open_cycle))
    notifications.fail = True
    failed = service.execute(
        scope, evaluated_at=_timestamp(open_cycle["evaluated_at"])
    )

    committed = unit_of_work.alerts.list_alerts()
    original_ids = {alert.alert_id for alert in committed}
    assert failed.notification_failures == 2
    assert len(committed) == 2
    assert {alert.status for alert in committed} == {AlertStatus.OPEN}
    assert unit_of_work.commits == 3

    notifications.fail = False
    replayed = service.execute(
        scope, evaluated_at=_timestamp(open_cycle["evaluated_at"])
    )

    assert (
        {alert.alert_id for alert in unit_of_work.alerts.list_alerts()}
        == original_ids
    )
    assert len(unit_of_work.alerts.list_alerts()) == 2
    assert replayed.transitions == ()
    assert replayed.notification_failures == 0
    assert len(notifications.attempts) == 2
    assert notifications.delivered == []
