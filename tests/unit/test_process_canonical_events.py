import json

import pytest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bootstrap.monitoring_demo import load_fixture, main
from canonical.application import ReplaySource
from monitoring.adapters.memory import InMemoryEventUnitOfWork
from monitoring.application import ProcessCanonicalEvents, ProcessingSummary
from monitoring.domain import CanonicalEvent, WindowAggregator
from shared_kernel import CanonicalStatus


FIXTURE = Path(__file__).parents[1] / "fixtures" / "monitoring" / "events.jsonl"
COMPUTED_AT = datetime(2026, 9, 25, 11, 0, tzinfo=UTC)


def test_replay_twice_is_idempotent_and_status_update_is_preserved() -> None:
    events = load_fixture(FIXTURE)
    unit_of_work = InMemoryEventUnitOfWork()
    processor = ProcessCanonicalEvents(unit_of_work)

    first = processor.execute(ReplaySource(events).replay(), computed_at=COMPUTED_AT)
    second = processor.execute(ReplaySource(events).replay(), computed_at=COMPUTED_AT)

    assert first.summary == ProcessingSummary(
        read=25,
        inserted=24,
        duplicated=1,
        filtered=2,
        failed=0,
    )
    assert second.summary == ProcessingSummary(
        read=25,
        inserted=0,
        duplicated=25,
        filtered=0,
        failed=0,
    )
    assert second.aggregates == first.aggregates
    transitions = [
        event
        for event in unit_of_work.events.list_events()
        if event.transaction.txn_id == "vn-transition"
    ]
    assert {event.transaction.status for event in transitions} == {
        CanonicalStatus.PENDING,
        CanonicalStatus.SUCCESS,
    }
    assert unit_of_work.commits == 2


def test_shuffle_does_not_change_aggregate_result() -> None:
    events = load_fixture(FIXTURE)
    ordered = ProcessCanonicalEvents(InMemoryEventUnitOfWork()).execute(
        ReplaySource(events).replay(),
        computed_at=COMPUTED_AT,
    )
    shuffled = ProcessCanonicalEvents(InMemoryEventUnitOfWork()).execute(
        ReplaySource(events, shuffle=True, seed=19).replay(),
        computed_at=COMPUTED_AT,
    )

    assert shuffled.summary == ordered.summary
    assert shuffled.aggregates == ordered.aggregates


def test_insert_failure_is_counted_without_losing_valid_event() -> None:
    first = load_fixture(FIXTURE)[0]
    collision = CanonicalEvent(
        first.event_id,
        replace(first.transaction, status=CanonicalStatus.FAILED),
    )
    unit_of_work = InMemoryEventUnitOfWork()

    output = ProcessCanonicalEvents(unit_of_work).execute(
        [first, collision],
        computed_at=COMPUTED_AT,
    )

    assert output.summary == ProcessingSummary(
        read=2,
        inserted=1,
        duplicated=0,
        filtered=0,
        failed=1,
    )
    assert unit_of_work.events.list_events() == (first,)


def test_cli_prints_replay_evidence_without_raw_payload(capsys) -> None:
    exit_code = main(
        [
            "--fixture",
            str(FIXTURE),
            "--replays",
            "2",
            "--shuffle",
            "--computed-at",
            "2026-09-25T11:00:00Z",
        ]
    )
    captured = capsys.readouterr().out
    document = json.loads(captured)

    assert exit_code == 0
    assert document["runs"] == [
        {
            "replay": 1,
            "read": 25,
            "inserted": 24,
            "duplicated": 1,
            "filtered": 2,
            "failed": 0,
            "late": 0,
            "expired": 0,
        },
        {
            "replay": 2,
            "read": 25,
            "inserted": 0,
            "duplicated": 25,
            "filtered": 0,
            "failed": 0,
            "late": 0,
            "expired": 0,
        },
    ]
    assert len(document["aggregates"]) == 10
    assert '"raw"' not in captured
    assert "p1_scenario" not in captured


def test_cli_supports_requested_window_size(capsys) -> None:
    assert main(["--fixture", str(FIXTURE), "--replays", "1", "--window-size", "15"]) == 0
    document = json.loads(capsys.readouterr().out)

    assert document["aggregates"]
    assert {row["window_size_minutes"] for row in document["aggregates"]} == {15}


def _event_at(event_id: str, txn_id: str, value: datetime) -> CanonicalEvent:
    base = load_fixture(FIXTURE)[0]
    return CanonicalEvent(
        event_id,
        replace(
            base.transaction,
            txn_id=txn_id,
            created_at=value,
            updated_at=value,
        ),
    )


def test_late_expired_duplicate_and_restart_are_idempotent() -> None:
    unit_of_work = InMemoryEventUnitOfWork(source_name="unit-source")
    processor = ProcessCanonicalEvents(unit_of_work)
    anchor_time = datetime(2026, 9, 25, 10, 30, tzinfo=UTC)
    anchor = _event_at("anchor", "anchor", anchor_time)
    first = processor.execute([anchor], computed_at=COMPUTED_AT)

    assert first.summary.inserted == 1
    assert unit_of_work.checkpoints[("VN", "")] == anchor_time

    late = _event_at("late", "late", anchor_time - timedelta(minutes=10))
    late_run = processor.execute([late], computed_at=COMPUTED_AT)
    assert late_run.summary.late == 1
    assert sum(row.late_event_count for row in late_run.aggregates) == 1

    replay = processor.execute([late], computed_at=COMPUTED_AT)
    assert replay.summary.duplicated == 1
    assert replay.summary.late == 0
    assert sum(row.late_event_count for row in replay.aggregates) == 1

    expired = _event_at(
        "expired", "expired", anchor_time - timedelta(hours=25)
    )
    expired_run = processor.execute([expired], computed_at=COMPUTED_AT)
    assert expired_run.summary.expired == 1
    assert len(unit_of_work.expired_audits) == 1
    expired_replay = processor.execute([expired], computed_at=COMPUTED_AT)
    assert expired_replay.summary.duplicated == 1
    assert len(unit_of_work.expired_audits) == 1

    before_commit = _event_at(
        "before-commit", "before-commit", anchor_time + timedelta(minutes=1)
    )
    with pytest.raises(RuntimeError, match="before commit"):
        processor.execute(
            [before_commit],
            computed_at=COMPUTED_AT,
            before_commit=lambda: (_ for _ in ()).throw(
                RuntimeError("before commit")
            ),
        )
    assert not unit_of_work.events.contains(before_commit)
    assert unit_of_work.checkpoints[("VN", "")] == anchor_time
    recovered_before = processor.execute([before_commit], computed_at=COMPUTED_AT)
    assert recovered_before.summary.inserted == 1

    after_commit = _event_at(
        "after-commit", "after-commit", anchor_time + timedelta(minutes=2)
    )
    with pytest.raises(RuntimeError, match="after commit"):
        processor.execute(
            [after_commit],
            computed_at=COMPUTED_AT,
            after_commit=lambda: (_ for _ in ()).throw(
                RuntimeError("after commit")
            ),
        )
    assert unit_of_work.events.contains(after_commit)
    assert unit_of_work.checkpoints[("VN", "")] == before_commit.event_time

    recovered_after = processor.execute([after_commit], computed_at=COMPUTED_AT)
    assert recovered_after.summary.duplicated == 1
    assert unit_of_work.checkpoints[("VN", "")] == after_commit.event_time
    assert len(unit_of_work.events.list_events()) == 4
    assert sum(row.total_event_count for row in recovered_after.aggregates) == 4
