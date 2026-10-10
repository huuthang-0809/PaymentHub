from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from monitoring.domain import (
    CanonicalEvent,
    EventTiming,
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
