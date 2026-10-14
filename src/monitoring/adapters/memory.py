from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from monitoring.domain import (
    AlertRecord,
    AlertStatus,
    AlertTransition,
    CanonicalEvent,
    EvaluationScope,
    EventTiming,
    HealthWindow,
    HealthDecisionPolicy,
    RuleConfig,
    RuleState,
    WatermarkPolicy,
    WindowAggregate,
)


class InMemoryCanonicalEventRepository:
    def __init__(self) -> None:
        self._events: dict[str, CanonicalEvent] = {}

    def contains(self, event: CanonicalEvent) -> bool:
        existing = self._events.get(event.event_id)
        if existing is None:
            return False
        if (
            existing.transaction != event.transaction
            or existing.parse_error != event.parse_error
        ):
            raise ValueError(
                f"event_id collision with different payload: {event.event_id}"
            )
        return True

    def insert_once(self, event: CanonicalEvent) -> bool:
        if self.contains(event):
            return False
        self._events[event.event_id] = event
        return True

    def list_events(self) -> tuple[CanonicalEvent, ...]:
        return tuple(self._events.values())


class InMemoryWindowAggregateRepository:
    def __init__(self) -> None:
        self._aggregates: dict[object, WindowAggregate] = {}

    def upsert_all(self, aggregates: Sequence[WindowAggregate]) -> None:
        for aggregate in aggregates:
            self._aggregates[aggregate.key] = aggregate

    def list_aggregates(self) -> tuple[WindowAggregate, ...]:
        return tuple(self._aggregates.values())


class InMemoryEventUnitOfWork:
    def __init__(
        self,
        repository: InMemoryCanonicalEventRepository | None = None,
        *,
        source_name: str = "memory",
    ) -> None:
        self.events = repository or InMemoryCanonicalEventRepository()
        self.aggregates = InMemoryWindowAggregateRepository()
        self.source_name = source_name
        self.checkpoints: dict[tuple[str, str], datetime] = {}
        self.expired_audits: dict[str, dict[str, Any]] = {}
        self.commits = 0
        self.checkpoint_writes = 0
        self._snapshot: dict[str, CanonicalEvent] = {}
        self._aggregate_snapshot: dict[object, WindowAggregate] = {}
        self._audit_snapshot: dict[str, dict[str, Any]] = {}
        self._baseline_checkpoints: dict[tuple[str, str], datetime] = {}
        self._staged_watermarks: dict[tuple[str, str], datetime] = {}
        self._committed = False
        self.summary: dict[str, Any] = {}

    @staticmethod
    def _scope(event: CanonicalEvent) -> tuple[str, str]:
        transaction = event.transaction
        return transaction.region.value, transaction.tenant_id or ""

    def __enter__(self) -> "InMemoryEventUnitOfWork":
        self._snapshot = dict(self.events._events)
        self._aggregate_snapshot = dict(self.aggregates._aggregates)
        self._audit_snapshot = dict(self.expired_audits)
        self._baseline_checkpoints = dict(self.checkpoints)
        self._staged_watermarks = {}
        self._committed = False
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        if not self._committed:
            self.events._events = self._snapshot
            self.aggregates._aggregates = self._aggregate_snapshot
            self.expired_audits = self._audit_snapshot
            self._staged_watermarks = {}

    def stage_watermark(self, event: CanonicalEvent) -> None:
        scope = self._scope(event)
        current = self._staged_watermarks.get(scope)
        if current is None or event.event_time > current:
            self._staged_watermarks[scope] = event.event_time

    def classify_timing(
        self, event: CanonicalEvent, policy: WatermarkPolicy
    ) -> EventTiming:
        scope = self._scope(event)
        timing = policy.classify(
            event.event_time, self._baseline_checkpoints.get(scope)
        )
        self.stage_watermark(event)
        return timing

    def audit_expired(self, event: CanonicalEvent) -> bool:
        if event.event_id in self.expired_audits:
            return False
        scope = self._scope(event)
        self.expired_audits[event.event_id] = {
            "source_name": self.source_name,
            "region": scope[0],
            "tenant_scope": scope[1],
            "txn_id": event.transaction.txn_id,
            "event_time": event.event_time,
            "watermark": self._baseline_checkpoints[scope],
            "classification": EventTiming.EXPIRED.value,
            "reason_code": "OUTSIDE_RETENTION",
        }
        return True

    def record_event_activity(self, event: CanonicalEvent) -> None:
        # The in-memory adapter has no independent freshness store.
        return None

    def commit(self) -> None:
        self._committed = True
        self.commits += 1

    def persist_checkpoint(self) -> None:
        if not self._committed:
            raise RuntimeError("checkpoint requires a committed unit of work")
        for scope, watermark in self._staged_watermarks.items():
            current = self.checkpoints.get(scope)
            if current is None or watermark > current:
                self.checkpoints[scope] = watermark
        self.checkpoint_writes += 1

    def record_summary(self, summary: Mapping[str, Any]) -> None:
        self.summary = dict(summary)


class InMemoryHealthMetricSource:
    def __init__(self) -> None:
        self._windows: dict[EvaluationScope, tuple[HealthWindow, ...]] = {}

    def set_windows(
        self, scope: EvaluationScope, windows: Sequence[HealthWindow]
    ) -> None:
        self._windows[scope] = tuple(windows)

    def list_closed_windows(
        self, scope: EvaluationScope, *, evaluated_at: datetime
    ) -> tuple[HealthWindow, ...]:
        return tuple(
            window
            for window in self._windows.get(scope, ())
            if window.window_end <= evaluated_at
        )


class InMemoryRuleConfigRepository:
    def __init__(self, configs: Sequence[RuleConfig]) -> None:
        self._configs = tuple(configs)

    def list_active(self) -> tuple[RuleConfig, ...]:
        return self._configs

    def decision_policy(self) -> HealthDecisionPolicy:
        return HealthDecisionPolicy()


class InMemoryRuleStateRepository:
    def __init__(self) -> None:
        self._states: dict[tuple[EvaluationScope, str, int], RuleState] = {}

    def list_for_scope(self, scope: EvaluationScope) -> tuple[RuleState, ...]:
        return tuple(
            state
            for (candidate, _, _), state in self._states.items()
            if candidate == scope
        )

    def save_all(
        self, scope: EvaluationScope, states: Sequence[RuleState]
    ) -> None:
        for state in states:
            self._states[(scope, state.rule_id, state.rule_version)] = state


class InMemoryAlertRepository:
    def __init__(self) -> None:
        self._alerts: dict[str, AlertRecord] = {}

    def get_open(
        self,
        scope: EvaluationScope,
        *,
        rule_id: str,
        rule_version: int,
    ) -> AlertRecord | None:
        matches = tuple(
            alert
            for alert in self._alerts.values()
            if alert.scope == scope
            and alert.rule_id == rule_id
            and alert.rule_version == rule_version
            and alert.status is AlertStatus.OPEN
        )
        if len(matches) > 1:
            raise RuntimeError("multiple OPEN alerts for one dedup key")
        return matches[0] if matches else None

    def save(self, alert: AlertRecord) -> None:
        self._alerts[alert.alert_id] = alert

    def get(self, alert_id: str) -> AlertRecord:
        return self._alerts[alert_id]

    def list_alerts(self) -> tuple[AlertRecord, ...]:
        return tuple(self._alerts.values())


class InMemoryAlertUnitOfWork:
    def __init__(self) -> None:
        self.alerts = InMemoryAlertRepository()
        self.rule_states = InMemoryRuleStateRepository()
        self.commits = 0
        self._evaluations: set[tuple[EvaluationScope, datetime]] = set()
        self._alert_snapshot: dict[str, AlertRecord] = {}
        self._state_snapshot: dict[
            tuple[EvaluationScope, str, int], RuleState
        ] = {}
        self._evaluation_snapshot: set[tuple[EvaluationScope, datetime]] = set()
        self._committed = False

    def __enter__(self) -> "InMemoryAlertUnitOfWork":
        self._alert_snapshot = dict(self.alerts._alerts)
        self._state_snapshot = dict(self.rule_states._states)
        self._evaluation_snapshot = set(self._evaluations)
        self._committed = False
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        if not self._committed:
            self.alerts._alerts = self._alert_snapshot
            self.rule_states._states = self._state_snapshot
            self._evaluations = self._evaluation_snapshot

    def evaluation_exists(
        self, scope: EvaluationScope, evaluated_at: datetime
    ) -> bool:
        return (scope, evaluated_at) in self._evaluations

    def record_evaluation(self, evaluation, transitions: Sequence[AlertTransition]) -> None:
        self._evaluations.add((evaluation.scope, evaluation.evaluated_at))

    def commit(self) -> None:
        self._committed = True
        self.commits += 1


class RecordingNotificationPort:
    def __init__(
        self,
        *,
        fail: bool = False,
        commit_probe: Callable[[], bool] | None = None,
    ) -> None:
        self.fail = fail
        self.commit_probe = commit_probe
        self.attempts: list[tuple[AlertRecord, AlertTransition]] = []
        self.delivered: list[tuple[AlertRecord, AlertTransition]] = []

    def send(self, alert: AlertRecord, transition: AlertTransition) -> None:
        if self.commit_probe is not None and not self.commit_probe():
            raise AssertionError("notification attempted before alert commit")
        self.attempts.append((alert, transition))
        if self.fail:
            raise RuntimeError("simulated notification failure")
        self.delivered.append((alert, transition))
