"""Acceptance tests for the nine-rule Week 5 provisional profile."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from monitoring.adapters.health_config import JsonRuleConfigRepository
from monitoring.adapters.memory import (
    InMemoryAlertUnitOfWork,
    InMemoryHealthMetricSource,
    InMemoryRuleConfigRepository,
    RecordingNotificationPort,
)
from monitoring.application import EvaluateProviderHealth
from monitoring.domain import (
    AlertAction,
    AlertStatus,
    EvaluationScope,
    HealthMetric,
    HealthWindow,
    RuleConfig,
)
from shared_kernel import Region

CONFIG = Path(__file__).parents[2] / "config/health-rules.week5-provisional.json"
SCOPE = EvaluationScope(Region.VN, "", "ONEPAY")
START = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)


def _window(config: RuleConfig, evaluated_at: datetime, *, breach: bool) -> HealthWindow:
    size = config.window_size_minutes or 15
    sample = max(config.minimum_sample or 100, 100)
    if config.metric is HealthMetric.SUCCESS_RATE:
        observed = (
            max(Decimal("0"), config.threshold - Decimal("0.10"))
            if breach else min(Decimal("1"), config.threshold + Decimal("0.05"))
        )
        success = int(observed * sample)
        failed = sample - success
        end = evaluated_at - timedelta(seconds=10)
    elif config.metric is HealthMetric.FAILURE_RATE:
        observed = (
            min(Decimal("1"), config.threshold + Decimal("0.10"))
            if breach else max(Decimal("0"), config.threshold - Decimal("0.05"))
        )
        failed = int(observed * sample)
        success = sample - failed
        end = evaluated_at - timedelta(seconds=10)
    else:
        success, failed = sample, 0
        lag = int(config.threshold) + 30 if breach else min(30, int(config.threshold))
        end = evaluated_at - timedelta(seconds=lag)
    start = end - timedelta(minutes=size)
    return HealthWindow(
        scope=SCOPE,
        window_size_minutes=size,
        window_start=start,
        window_end=end,
        operation_event_count=sample,
        success_count=success,
        failed_count=failed,
        data_as_of=end - timedelta(seconds=1),
    )


def _execute(service, source, config, evaluated_at, *, breach):
    source.set_windows(SCOPE, (_window(config, evaluated_at, breach=breach),))
    return service.execute(SCOPE, evaluated_at=evaluated_at)


def test_week5_profile_has_nine_unique_versioned_rules_and_runbooks() -> None:
    configs = JsonRuleConfigRepository(
        CONFIG, required_profile="WEEK5_PROVISIONAL"
    ).list_active()
    assert len(configs) == 9
    assert len({(item.rule_id, item.rule_version) for item in configs}) == 9
    assert all(item.runbook_ref.startswith("docs/runbooks/") for item in configs)
    assert {item.metric for item in configs} == {
        HealthMetric.SUCCESS_RATE,
        HealthMetric.FAILURE_RATE,
        HealthMetric.FRESHNESS_LAG_SECONDS,
    }


def test_all_nine_rules_dedupe_cooldown_and_recover() -> None:
    configs = JsonRuleConfigRepository(
        CONFIG, required_profile="WEEK5_PROVISIONAL"
    ).list_active()

    for index, config in enumerate(configs):
        source = InMemoryHealthMetricSource()
        uow = InMemoryAlertUnitOfWork()
        notifications = RecordingNotificationPort()
        service = EvaluateProviderHealth(
            source,
            InMemoryRuleConfigRepository((config,)),
            uow,
            notifications,
        )
        now = START + timedelta(days=index)

        for attempt in range(config.breach_windows):
            now += timedelta(seconds=60)
            output = _execute(service, source, config, now, breach=True)
            if attempt < config.breach_windows - 1:
                assert output.transitions == ()
        assert len(output.transitions) == 1
        opened = output.transitions[0]
        assert opened.action is AlertAction.OPEN
        alert_id = opened.alert_id
        assert opened.notification_due is True

        now += timedelta(seconds=60)
        continued = _execute(service, source, config, now, breach=True)
        assert len(continued.transitions) == 1
        assert continued.transitions[0].action is AlertAction.UPDATE
        assert continued.transitions[0].alert_id == alert_id
        assert continued.transitions[0].notification_due is False
        assert len(uow.alerts.list_alerts()) == 1

        replay = _execute(service, source, config, now, breach=True)
        assert replay.replayed is True
        assert replay.transitions == ()
        assert len(uow.alerts.list_alerts()) == 1

        for attempt in range(config.recovery_windows):
            now += timedelta(seconds=60)
            recovered = _execute(service, source, config, now, breach=False)
            if attempt < config.recovery_windows - 1:
                assert recovered.transitions == ()
        assert len(recovered.transitions) == 1
        assert recovered.transitions[0].action is AlertAction.RESOLVE
        assert recovered.transitions[0].alert_id == alert_id
        alert = uow.alerts.list_alerts()[0]
        assert alert.status is AlertStatus.RESOLVED
        assert len(notifications.delivered) == 2
