from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime

from monitoring.domain import (
    CanonicalEvent,
    EventTiming,
    WatermarkPolicy,
    WindowAggregate,
    WindowAggregator,
)

from .ports import EventUnitOfWork


@dataclass(frozen=True, slots=True)
class ProcessingSummary:
    read: int
    inserted: int
    duplicated: int
    filtered: int
    failed: int
    late: int = 0
    expired: int = 0


@dataclass(frozen=True, slots=True)
class ProcessingOutput:
    summary: ProcessingSummary
    aggregates: tuple[WindowAggregate, ...]


class ProcessCanonicalEvents:
    def __init__(
        self,
        unit_of_work: EventUnitOfWork,
        aggregator: WindowAggregator | None = None,
        watermark_policy: WatermarkPolicy | None = None,
    ) -> None:
        self.unit_of_work = unit_of_work
        self.aggregator = aggregator or WindowAggregator()
        self.watermark_policy = watermark_policy or WatermarkPolicy()

    def execute(
        self,
        events: Iterable[CanonicalEvent],
        *,
        computed_at: datetime,
        before_commit: Callable[[], None] | None = None,
        after_commit: Callable[[], None] | None = None,
    ) -> ProcessingOutput:
        read = inserted = duplicated = filtered = failed = late = expired = 0

        with self.unit_of_work as unit_of_work:
            for source_event in events:
                read += 1
                try:
                    if unit_of_work.events.contains(source_event):
                        unit_of_work.stage_watermark(source_event)
                        duplicated += 1
                        continue
                    timing = unit_of_work.classify_timing(
                        source_event, self.watermark_policy
                    )
                    if timing is EventTiming.EXPIRED:
                        if unit_of_work.audit_expired(source_event):
                            expired += 1
                        else:
                            duplicated += 1
                        continue
                    event = replace(
                        source_event, is_late=timing is EventTiming.LATE
                    )
                    was_inserted = unit_of_work.events.insert_once(event)
                except Exception:
                    failed += 1
                    continue

                if not was_inserted:
                    duplicated += 1
                    continue

                inserted += 1
                if timing is EventTiming.LATE:
                    late += 1
                transaction = event.transaction
                if transaction.is_deleted or transaction.is_test_data:
                    filtered += 1

            aggregates = self.aggregator.aggregate(
                unit_of_work.events.list_events(),
                computed_at=computed_at,
            )
            summary = ProcessingSummary(
                read=read,
                inserted=inserted,
                duplicated=duplicated,
                filtered=filtered,
                failed=failed,
                late=late,
                expired=expired,
            )
            unit_of_work.record_summary(
                {
                    "read": summary.read,
                    "inserted": summary.inserted,
                    "duplicated": summary.duplicated,
                    "filtered": summary.filtered,
                    "failed": summary.failed,
                    "late": summary.late,
                    "expired": summary.expired,
                    "aggregate_count": len(aggregates),
                }
            )
            unit_of_work.aggregates.upsert_all(aggregates)
            if before_commit is not None:
                before_commit()
            unit_of_work.commit()
            if after_commit is not None:
                after_commit()
            unit_of_work.persist_checkpoint()

        return ProcessingOutput(summary=summary, aggregates=aggregates)
