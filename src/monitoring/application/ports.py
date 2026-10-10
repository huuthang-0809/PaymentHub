from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, Self

from canonical.domain import CanonicalTransaction
from monitoring.domain import (
    AlertRecord,
    AlertTransition,
    CanonicalEvent,
    EvaluationScope,
    HealthEvaluation,
    HealthWindow,
    RuleConfig,
    RuleState,
    WindowAggregate,
)
from shared_kernel import CanonicalStatus, Region

if TYPE_CHECKING:
    from .query import WindowMetricPage, WindowMetricQuery


class TransactionQueryRepository(Protocol):
    def is_ready(self) -> bool: ...

    def list_transactions(
        self,
        *,
        region: Region | None,
        status: CanonicalStatus | None,
        limit: int,
    ) -> Sequence[CanonicalTransaction]: ...


class WindowMetricQueryRepository(Protocol):
    def list_window_metrics(self, query: "WindowMetricQuery") -> "WindowMetricPage": ...


class MonitoringQueryRepository(
    TransactionQueryRepository, WindowMetricQueryRepository, Protocol
):
    pass


class CanonicalEventRepository(Protocol):
    def contains(self, event: CanonicalEvent) -> bool: ...

    def insert_once(self, event: CanonicalEvent) -> bool: ...

    def list_events(self) -> Sequence[CanonicalEvent]: ...


class WindowAggregateRepository(Protocol):
    def upsert_all(self, aggregates: Sequence[WindowAggregate]) -> None: ...


class EventUnitOfWork(Protocol):
    events: CanonicalEventRepository
    aggregates: WindowAggregateRepository

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None: ...

    def stage_watermark(self, event: CanonicalEvent) -> None: ...

    def classify_timing(self, event: CanonicalEvent, policy: Any) -> Any: ...

    def audit_expired(self, event: CanonicalEvent) -> bool: ...

    def commit(self) -> None: ...

    def persist_checkpoint(self) -> None: ...

    def record_summary(self, summary: Mapping[str, Any]) -> None: ...


class HealthMetricSource(Protocol):
    def list_closed_windows(
        self, scope: EvaluationScope, *, evaluated_at: datetime
    ) -> Sequence[HealthWindow]: ...


class RuleConfigRepository(Protocol):
    def list_active(self) -> Sequence[RuleConfig]: ...


class RuleStateRepository(Protocol):
    def list_for_scope(self, scope: EvaluationScope) -> Sequence[RuleState]: ...

    def save_all(
        self, scope: EvaluationScope, states: Sequence[RuleState]
    ) -> None: ...


class AlertRepository(Protocol):
    def get_open(
        self,
        scope: EvaluationScope,
        *,
        rule_id: str,
        rule_version: int,
    ) -> AlertRecord | None: ...

    def save(self, alert: AlertRecord) -> None: ...

    def get(self, alert_id: str) -> AlertRecord: ...


class NotificationPort(Protocol):
    def send(self, alert: AlertRecord, transition: AlertTransition) -> None: ...


class AlertUnitOfWork(Protocol):
    alerts: AlertRepository
    rule_states: RuleStateRepository

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None: ...

    def evaluation_exists(
        self, scope: EvaluationScope, evaluated_at: datetime
    ) -> bool: ...

    def record_evaluation(
        self, evaluation: "HealthEvaluation", transitions: Sequence[AlertTransition]
    ) -> None: ...

    def commit(self) -> None: ...
