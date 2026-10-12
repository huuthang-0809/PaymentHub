"""Reproducible W4 replay benchmark.

Destructive: DATABASE_URL must point to a disposable PostgreSQL database.
"""

from __future__ import annotations

import json
import os
import platform
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from bootstrap.monitoring_demo import load_fixture
from monitoring.adapters.postgres import PostgresEventUnitOfWork
from monitoring.application import ProcessCanonicalEvents

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests/fixtures/monitoring/events.jsonl"
OUTPUT = Path(__file__).with_name("result-w4.json")
COMPUTED_AT = datetime(2026, 9, 25, 11, 0, tzinfo=UTC)


def psycopg_url(value: str) -> str:
    parsed = make_url(value)
    return parsed.set(drivername="postgresql").render_as_string(hide_password=False)


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url or os.environ.get("PAYMENTHUB_BENCH_DESTRUCTIVE") != "1":
        raise SystemExit(
            "set DATABASE_URL to a disposable DB and PAYMENTHUB_BENCH_DESTRUCTIVE=1"
        )
    with psycopg.connect(psycopg_url(url), autocommit=True) as connection:
        connection.execute("DROP SCHEMA IF EXISTS public CASCADE")
        connection.execute("CREATE SCHEMA public")
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")

    events = load_fixture(FIXTURE)
    expected_unique = len({event.event_id for event in events})
    started = time.perf_counter()
    first = ProcessCanonicalEvents(
        PostgresEventUnitOfWork(url, source_name="w4-benchmark")
    ).execute(events, computed_at=COMPUTED_AT)
    replay = ProcessCanonicalEvents(
        PostgresEventUnitOfWork(url, source_name="w4-benchmark")
    ).execute(events, computed_at=COMPUTED_AT)
    elapsed = time.perf_counter() - started

    with psycopg.connect(psycopg_url(url)) as connection:
        canonical_count = connection.execute(
            "SELECT count(*) FROM canonical_event"
        ).fetchone()[0]
        aggregate_total = connection.execute(
            "SELECT coalesce(sum(total_count), 0) FROM window_aggregate"
        ).fetchone()[0]
        null_lineage = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM canonical_event WHERE job_run_id IS NULL) +
              (SELECT count(*) FROM window_aggregate WHERE job_run_id IS NULL) +
              (SELECT count(*) FROM late_event_audit WHERE job_run_id IS NULL)
            """
        ).fetchone()[0]
        jobs_complete = connection.execute(
            """SELECT count(*) FROM job_run_log
               WHERE source_name = 'w4-benchmark' AND status = 'COMPLETED'
                 AND data_from IS NOT NULL AND data_to IS NOT NULL
                 AND source_checkpoint <> '{}'::jsonb"""
        ).fetchone()[0]

    expected_aggregate_total = first.summary.inserted - first.summary.filtered
    result = {
        "schema_version": 1,
        "benchmark": "p1-w4-replay-lineage",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "fixture": "tests/fixtures/monitoring/events.jsonl",
        "runs": 2,
        "input_records_per_run": len(events),
        "expected_unique_events": expected_unique,
        "canonical_rows": canonical_count,
        "aggregate_total_count": aggregate_total,
        "expected_aggregate_total_count": expected_aggregate_total,
        "redeliveries_deduplicated": replay.summary.duplicated,
        "lost": max(0, expected_unique - canonical_count),
        "duplicated": max(0, canonical_count - expected_unique)
        + max(0, aggregate_total - expected_aggregate_total),
        "null_job_run_id_rows": null_lineage,
        "complete_job_runs": jobs_complete,
        "elapsed_seconds": round(elapsed, 6),
        "environment": {
            "python": platform.python_version(),
            "postgres": "15",
        },
        "pass": (
            canonical_count == expected_unique
            and aggregate_total == expected_aggregate_total
            and null_lineage == 0
            and jobs_complete == 2
            and replay.summary.inserted == 0
        ),
    }
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
