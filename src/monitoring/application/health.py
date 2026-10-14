from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from monitoring.domain import (
    AlertAction,
    AlertRecord,
    AlertStatus,
    AlertTransition,
    EvaluationScope,
    HealthEvaluation,
    HealthEvaluator,
    MetricRule,
)
from shared_kernel import ensure_utc

from .ports import (
    AlertUnitOfWork,
    HealthMetricSource,
    NotificationPort,
    RuleConfigRepository,
)


@dataclass(frozen=True, slots=True)
class HealthEvaluationOutput:
    evaluation: HealthEvaluation
    transitions: tuple[AlertTransition, ...]
    notification_failures: int
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _PendingNotification:
    alert: AlertRecord
    transition: AlertTransition


class AlertManager:
    def __init__(self, id_factory: Callable[[], str] | None = None) -> None:
        self._id_factory = id_factory or (lambda: str(uuid4()))

    def apply(
        self,
        evaluation: HealthEvaluation,
        unit_of_work: AlertUnitOfWork,
    ) -> tuple[tuple[AlertTransition, ...], tuple[_PendingNotification, ...]]:
        transitions: list[AlertTransition] = []
        notifications: list[_PendingNotification] = []
        now = evaluation.evaluated_at

        for result in evaluation.evaluations:
            config = result.config
            current = unit_of_work.alerts.get_open(
                evaluation.scope,
                rule_id=config.rule_id,
                rule_version=config.rule_version,
            )

            if result.breach_confirmed:
                action = (
                    AlertAction.OPEN if current is None else AlertAction.UPDATE
                )
                occurrence_count = (
                    1 if current is None else current.occurrence_count + 1
                )
                notification_due = self._notification_due(
                    current, now, config.cooldown_seconds
                )
                if current is None:
                    alert = AlertRecord(
                        alert_id=self._id_factory(),
                        scope=evaluation.scope,
                        rule_id=config.rule_id,
                        rule_version=config.rule_version,
                        status=AlertStatus.OPEN,
                        severity=config.severity,
                        reason=result.reason,
                        occurrence_count=occurrence_count,
                        cooldown_seconds=config.cooldown_seconds,
                        opened_at=now,
                        last_seen_at=now,
                        last_evaluated_at=now,
                        last_notification_attempt_at=now if notification_due else None,
                    )
                else:
                    alert = replace(
                        current,
                        reason=result.reason,
                        occurrence_count=occurrence_count,
                        last_seen_at=now,
                        last_evaluated_at=now,
                        last_notification_attempt_at=(
                            now
                            if notification_due
                            else current.last_notification_attempt_at
                        ),
                    )
                transition = self._transition(alert, action, notification_due)
                unit_of_work.alerts.save(alert)
                transitions.append(transition)
                if notification_due:
                    notifications.append(_PendingNotification(alert, transition))
                continue

            if current is not None and result.recovery_confirmed:
                alert = replace(
                    current,
                    status=AlertStatus.RESOLVED,
                    reason=result.reason,
                    last_evaluated_at=now,
                    last_notification_attempt_at=now,
                    resolved_at=now,
                )
                transition = self._transition(alert, AlertAction.RESOLVE, True)
                unit_of_work.alerts.save(alert)
                transitions.append(transition)
                notifications.append(_PendingNotification(alert, transition))
                continue

            if current is not None:
                unit_of_work.alerts.save(
                    replace(current, reason=result.reason, last_evaluated_at=now)
                )

        return tuple(transitions), tuple(notifications)

    @staticmethod
    def _notification_due(
        current: AlertRecord | None,
        now: datetime,
        cooldown_seconds: int,
    ) -> bool:
        if current is None:
            return True
        anchor = current.last_notification_attempt_at or current.opened_at
        return now - anchor >= timedelta(seconds=cooldown_seconds)

    @staticmethod
    def _transition(
        alert: AlertRecord,
        action: AlertAction,
        notification_due: bool,
    ) -> AlertTransition:
        return AlertTransition(
            alert_id=alert.alert_id,
            scope=alert.scope,
            rule_id=alert.rule_id,
            rule_version=alert.rule_version,
            action=action,
            occurrence_count=alert.occurrence_count,
            notification_due=notification_due,
        )


def _system_clock() -> datetime:
    return datetime.now(UTC)


class EvaluateProviderHealth:
    def __init__(
        self,
        metric_source: HealthMetricSource,
        config_repository: RuleConfigRepository,
        unit_of_work: AlertUnitOfWork,
        notification_port: NotificationPort,
        *,
        clock: Callable[[], datetime] = _system_clock,
        alert_manager: AlertManager | None = None,
    ) -> None:
        self.metric_source = metric_source
        self.config_repository = config_repository
        self.unit_of_work = unit_of_work
        self.notification_port = notification_port
        self.clock = clock
        self.alert_manager = alert_manager or AlertManager()

    def execute(
        self,
        scope: EvaluationScope,
        *,
        evaluated_at: datetime | None = None,
    ) -> HealthEvaluationOutput:
        evaluated_at = ensure_utc(evaluated_at or self.clock())
        configs = tuple(self.config_repository.list_active())
        evaluator = HealthEvaluator(
            (MetricRule(config) for config in configs),
            decision_policy=self.config_repository.decision_policy(),
        )
        windows = tuple(
            self.metric_source.list_closed_windows(scope, evaluated_at=evaluated_at)
        )

        with self.unit_of_work as unit_of_work:
            prior_states = unit_of_work.rule_states.list_for_scope(scope)
            replayed = unit_of_work.evaluation_exists(scope, evaluated_at)
            evaluation = evaluator.evaluate(
                scope,
                windows,
                prior_states=prior_states,
                evaluated_at=evaluated_at,
            )
            if replayed:
                transitions = ()
                notifications = ()
            else:
                unit_of_work.rule_states.save_all(scope, evaluation.next_states)
                transitions, notifications = self.alert_manager.apply(
                    evaluation, unit_of_work
                )
                unit_of_work.record_evaluation(evaluation, transitions)
            unit_of_work.commit()

        failures = 0
        for pending in notifications:
            try:
                self.notification_port.send(pending.alert, pending.transition)
            except Exception:
                failures += 1

        return HealthEvaluationOutput(
            evaluation=evaluation,
            transitions=transitions,
            notification_failures=failures,
            replayed=replayed,
        )
