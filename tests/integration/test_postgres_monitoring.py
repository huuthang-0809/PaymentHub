"""PostgreSQL transaction and concurrency tests for monitoring persistence."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from bootstrap.monitoring_demo import load_fixture
from monitoring.adapters.postgres import (
    PostgresEventUnitOfWork,
    PostgresTransactionRepository,
    PostgresWindowAggregateRepository,
)
from monitoring.application import ProcessCanonicalEvents, WindowMetricQuery
from monitoring.domain import CanonicalEvent, WindowAggregator
from shared_kernel import Region


pytestmark = pytest.mark.integration
FIXTURE = Path(__file__).parents[1] / "fixtures" / "monitoring" / "events.jsonl"
COMPUTED_AT = datetime(2026, 9, 25, 11, 0, tzinfo=UTC)


def _database_url() -> str:
    if os.getenv("PAYMENTHUB_RUN_DB_INTEGRATION") != "1":
        pytest.skip("set PAYMENTHUB_RUN_DB_INTEGRATION=1 to run destructive DB tests")
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL must point to a disposable PostgreSQL database")
    return url


def _psycopg_url(url: str) -> str:
    parsed = make_url(url)
    return parsed.set(drivername="postgresql").render_as_string(hide_password=False)


def _reset_and_migrate(url: str) -> None:
    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        connection.execute("DROP SCHEMA IF EXISTS public CASCADE")
        connection.execute("CREATE SCHEMA public")
    previous_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url


@pytest.fixture
def postgres_url() -> str:
    url = _database_url()
    _reset_and_migrate(url)
    yield url
    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        connection.execute("DROP SCHEMA IF EXISTS public CASCADE")
        connection.execute("CREATE SCHEMA public")


def test_rerun_does_not_change_events_or_aggregates(postgres_url: str) -> None:
    events = load_fixture(FIXTURE)[:3]
    first_uow = PostgresEventUnitOfWork(postgres_url, source_name="integration")
    first = ProcessCanonicalEvents(first_uow).execute(
        events[:2], computed_at=COMPUTED_AT
    )
    second_uow = PostgresEventUnitOfWork(postgres_url, source_name="integration")
    second = ProcessCanonicalEvents(second_uow).execute(
        events[2:], computed_at=COMPUTED_AT
    )
    replay_uow = PostgresEventUnitOfWork(postgres_url, source_name="integration")
    replay = ProcessCanonicalEvents(replay_uow).execute(
        events, computed_at=COMPUTED_AT
    )

    assert first.summary.inserted == 2
    assert second.summary.inserted == 1
    assert replay.summary.inserted == 0
    assert replay.summary.duplicated == 3
    with psycopg.connect(_psycopg_url(postgres_url)) as connection:
        event_count = connection.execute(
            "SELECT count(*) FROM canonical_event"
        ).fetchone()[0]
        aggregate_rows = connection.execute(
            """
            SELECT sum(total_count), count(*)
            FROM window_aggregate
            """
        ).fetchone()
        jobs = connection.execute(
            """
            SELECT status, metrics->>'inserted'
            FROM job_run_log
                WHERE job_name = 'monitoring-canonical-events'
                ORDER BY started_at
            """
        ).fetchall()
    assert event_count == 3
    assert aggregate_rows == (3, 1)
    assert jobs == [
        ("COMPLETED", "2"),
        ("COMPLETED", "1"),
        ("COMPLETED", "0"),
    ]


def test_window_query_matches_committed_data_after_replay(postgres_url: str) -> None:
    events = load_fixture(FIXTURE)
    ProcessCanonicalEvents(
        PostgresEventUnitOfWork(postgres_url, source_name="integration")
    ).execute(events, computed_at=COMPUTED_AT)

    repository = PostgresTransactionRepository(postgres_url)
    query = WindowMetricQuery(region=Region.VN, window_size=5, limit=500)
    before_replay = repository.list_window_metrics(query)

    replay = ProcessCanonicalEvents(
        PostgresEventUnitOfWork(postgres_url, source_name="integration")
    ).execute(events, computed_at=COMPUTED_AT)
    after_replay = repository.list_window_metrics(query)

    with psycopg.connect(_psycopg_url(postgres_url)) as connection:
        database_totals = connection.execute(
            """
            SELECT count(*), sum(total_count), max(computed_at)
            FROM window_aggregate
            WHERE region = 'VN' AND window_size_minutes = 5
            """
        ).fetchone()

    def stable_metrics(page):
        return [
            (
                item.window_size_minutes, item.window_start, item.window_end,
                item.region, item.tenant_id, item.provider, item.txn_type,
                item.instrument, item.channel_raw, item.currency,
                item.total_event_count, item.operation_event_count,
                item.success_count, item.failed_count, item.pending_count,
                item.unknown_count, item.success_rate, item.failed_rate,
                item.unknown_rate, item.monetary_count,
                item.monetary_amount_minor, item.invalid_amount_count,
                item.missing_currency_count, item.parse_error_count,
                item.late_event_count,
                item.non_terminal_count, item.pending_age_lt_5m_count,
                item.pending_age_5_to_15m_count,
                item.pending_age_15_to_60m_count,
                item.pending_age_gte_60m_count,
                item.oldest_pending_age_seconds, item.freshness_lag_seconds,
                item.source_event_count,
                item.last_event_at,
            )
            for item in page.items
        ]

    assert replay.summary.inserted == 0
    assert replay.summary.duplicated == len(events)
    assert sum(item.parse_error_count for item in after_replay.items) == 1
    assert len(after_replay.items) == database_totals[0]
    assert sum(item.total_event_count for item in after_replay.items) == database_totals[1]
    assert after_replay.data_as_of == database_totals[2]
    assert stable_metrics(after_replay) == stable_metrics(before_replay)


def test_aggregate_failure_rolls_back_events_and_marks_job_failed(
    postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = load_fixture(FIXTURE)[0]
    unit_of_work = PostgresEventUnitOfWork(postgres_url, source_name="integration")

    def fail_write(self, aggregates):
        raise RuntimeError("injected aggregate failure")

    monkeypatch.setattr(PostgresWindowAggregateRepository, "upsert_all", fail_write)
    with pytest.raises(RuntimeError, match="injected aggregate failure"):
        ProcessCanonicalEvents(unit_of_work).execute(
            [event], computed_at=COMPUTED_AT
        )

    with psycopg.connect(_psycopg_url(postgres_url)) as connection:
        event_count = connection.execute(
            "SELECT count(*) FROM canonical_event"
        ).fetchone()[0]
        aggregate_count = connection.execute(
            "SELECT count(*) FROM window_aggregate"
        ).fetchone()[0]
        job = connection.execute(
            """
            SELECT status, error_code, metrics FROM job_run_log
            WHERE job_run_id = %s
            """,
            (unit_of_work.job_run_id,),
        ).fetchone()
    assert (event_count, aggregate_count) == (0, 0)
    assert job[0:2] == ("FAILED", "RuntimeError")
    assert job[2]["inserted"] == 0
    assert job[2]["attempted_inserted"] == 1
    assert job[2]["aggregate_count"] == 0
    assert job[2]["rolled_back"] is True


def test_two_workers_processing_same_event_do_not_double_count(
    postgres_url: str,
) -> None:
    event = load_fixture(FIXTURE)[0]
    barrier = Barrier(2)

    def process() -> tuple[int, int]:
        barrier.wait()
        output = ProcessCanonicalEvents(
            PostgresEventUnitOfWork(postgres_url, source_name="integration")
        ).execute([event], computed_at=COMPUTED_AT)
        return output.summary.inserted, output.summary.duplicated

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: process(), range(2)))

    assert sorted(results) == [(0, 1), (1, 0)]
    with psycopg.connect(_psycopg_url(postgres_url)) as connection:
        event_count = connection.execute(
            "SELECT count(*) FROM canonical_event"
        ).fetchone()[0]
        aggregate_total = connection.execute(
            "SELECT sum(total_count) FROM window_aggregate"
        ).fetchone()[0]
    assert event_count == 1
    assert aggregate_total == 1


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


def test_postgres_late_expired_and_checkpoint_restart_safety(
    postgres_url: str,
) -> None:
    source_name = "t9-integration"
    anchor_time = datetime(2026, 9, 25, 10, 30, tzinfo=UTC)

    def process(events, **hooks):
        return ProcessCanonicalEvents(
            PostgresEventUnitOfWork(postgres_url, source_name=source_name)
        ).execute(events, computed_at=COMPUTED_AT, **hooks)

    anchor = _event_at("t9-anchor", "t9-anchor", anchor_time)
    process([anchor])

    late = _event_at(
        "t9-late", "t9-late", anchor_time - timedelta(minutes=10)
    )
    late_run = process([late])
    assert late_run.summary.late == 1
    assert sum(row.late_event_count for row in late_run.aggregates) == 1
    late_replay = process([late])
    assert late_replay.summary.duplicated == 1
    assert sum(row.late_event_count for row in late_replay.aggregates) == 1

    expired = _event_at(
        "t9-expired", "t9-expired", anchor_time - timedelta(hours=25)
    )
    assert process([expired]).summary.expired == 1
    assert process([expired]).summary.duplicated == 1

    before = _event_at(
        "t9-before", "t9-before", anchor_time + timedelta(minutes=1)
    )
    with pytest.raises(RuntimeError, match="before commit"):
        process(
            [before],
            before_commit=lambda: (_ for _ in ()).throw(
                RuntimeError("before commit")
            ),
        )
    assert process([before]).summary.inserted == 1

    after = _event_at(
        "t9-after", "t9-after", anchor_time + timedelta(minutes=2)
    )
    with pytest.raises(RuntimeError, match="after commit"):
        process(
            [after],
            after_commit=lambda: (_ for _ in ()).throw(
                RuntimeError("after commit")
            ),
        )

    with psycopg.connect(_psycopg_url(postgres_url)) as connection:
        checkpoint_before_recovery = connection.execute(
            """
            SELECT watermark FROM source_checkpoint
            WHERE source_name = %s AND region = 'VN' AND tenant_scope = ''
            """,
            (source_name,),
        ).fetchone()[0]
    assert checkpoint_before_recovery == before.event_time

    recovered = process([after])
    assert recovered.summary.duplicated == 1
    expected = WindowAggregator().aggregate(
        [anchor, replace(late, is_late=True), before, after],
        computed_at=COMPUTED_AT,
    )
    assert recovered.aggregates == expected

    with psycopg.connect(_psycopg_url(postgres_url)) as connection:
        canonical_count = connection.execute(
            "SELECT count(*) FROM canonical_event"
        ).fetchone()[0]
        aggregate = connection.execute(
            "SELECT sum(total_count), sum(late_event_count) FROM window_aggregate"
        ).fetchone()
        audit = connection.execute(
            """
            SELECT count(*), min(reason_code) FROM late_event_audit
            WHERE source_name = %s
            """,
            (source_name,),
        ).fetchone()
        checkpoint = connection.execute(
            """
            SELECT watermark FROM source_checkpoint
            WHERE source_name = %s AND region = 'VN' AND tenant_scope = ''
            """,
            (source_name,),
        ).fetchone()[0]

    assert canonical_count == 4
    assert aggregate == (4, 1)
    assert audit == (1, "OUTSIDE_RETENTION")
    assert checkpoint == after.event_time


def test_real_process_kill_after_commit_resumes_without_loss_or_double_count(
    postgres_url: str,
) -> None:
    """A hard process exit after DB commit behaves like redelivery before ACK."""
    code = textwrap.dedent(
        """
        import os
        import signal
        from datetime import UTC, datetime
        from pathlib import Path
        from bootstrap.monitoring_demo import load_fixture
        from monitoring.adapters.postgres import PostgresEventUnitOfWork
        from monitoring.application import ProcessCanonicalEvents

        url = os.environ["CRASH_TEST_DATABASE_URL"]
        fixture = Path("tests/fixtures/monitoring/events.jsonl")
        event = load_fixture(fixture)[0]
        ProcessCanonicalEvents(
            PostgresEventUnitOfWork(url, source_name="sigkill-integration")
        ).execute(
            [event],
            computed_at=datetime(2026, 9, 25, 11, 0, tzinfo=UTC),
            after_commit=lambda: os.kill(os.getpid(), signal.SIGKILL),
        )
        """
    )
    environment = dict(os.environ)
    environment["CRASH_TEST_DATABASE_URL"] = postgres_url
    crashed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parents[2],
        env=environment,
        check=False,
    )
    assert crashed.returncode == -9

    event = load_fixture(FIXTURE)[0]
    recovered = ProcessCanonicalEvents(
        PostgresEventUnitOfWork(postgres_url, source_name="sigkill-integration")
    ).execute([event], computed_at=COMPUTED_AT)
    assert recovered.summary.inserted == 0
    assert recovered.summary.duplicated == 1

    with psycopg.connect(_psycopg_url(postgres_url)) as connection:
        counts = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM canonical_event),
              (SELECT sum(total_count) FROM window_aggregate),
              (SELECT count(*) FROM canonical_event WHERE job_run_id IS NULL),
              (SELECT count(*) FROM window_aggregate WHERE job_run_id IS NULL)
            """
        ).fetchone()
        jobs = connection.execute(
            """
            SELECT status, data_from IS NOT NULL, data_to IS NOT NULL,
                   source_checkpoint <> '{}'::jsonb
            FROM job_run_log
            WHERE source_name = 'sigkill-integration'
            ORDER BY started_at
            """
        ).fetchall()
    assert counts == (1, 1, 0, 0)
    assert jobs == [
        ("COMPLETED", True, True, True),
        ("COMPLETED", True, True, True),
    ]
