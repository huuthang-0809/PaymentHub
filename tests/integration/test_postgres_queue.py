"""Integration tests for the durable monitoring queue."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from monitoring.adapters.postgres_queue import PostgresMonitoringQueue

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _url() -> str:
    if os.getenv("PAYMENTHUB_RUN_DB_INTEGRATION") != "1":
        pytest.skip("set PAYMENTHUB_RUN_DB_INTEGRATION=1")
    value = os.getenv("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL is required")
    return value


def _psycopg_url(value: str) -> str:
    parsed = make_url(value)
    return parsed.set(drivername="postgresql").render_as_string(hide_password=False)


@pytest.fixture(scope="module")
def queue_url() -> str:
    value = _url()
    with psycopg.connect(_psycopg_url(value), autocommit=True) as connection:
        connection.execute("DROP SCHEMA IF EXISTS public CASCADE")
        connection.execute("CREATE SCHEMA public")
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = value
    try:
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
    yield value


@pytest.fixture(autouse=True)
def empty_queue(queue_url: str) -> None:
    with psycopg.connect(_psycopg_url(queue_url)) as connection:
        connection.execute("TRUNCATE monitoring_queue_message")


def _state(url: str) -> tuple:
    with psycopg.connect(_psycopg_url(url)) as connection:
        return connection.execute(
            """SELECT status, attempts, last_error_code, last_error_class
               FROM monitoring_queue_message"""
        ).fetchone()


def test_queue_claim_and_ack_completes_message(queue_url: str) -> None:
    queue = PostgresMonitoringQueue(queue_url)
    message_id, inserted = queue.enqueue("event:ack", {"event_id": "ack"})
    claimed = queue.claim(now=NOW)
    assert inserted and claimed is not None and claimed.message_id == message_id
    queue.ack(message_id, now=NOW)
    assert _state(queue_url) == ("COMPLETED", 1, None, None)


def test_queue_duplicate_publish_is_idempotent(queue_url: str) -> None:
    queue = PostgresMonitoringQueue(queue_url)
    first_id, first_inserted = queue.enqueue("event:same", {"version": 1})
    second_id, second_inserted = queue.enqueue("event:same", {"version": 2})
    assert first_inserted is True
    assert second_inserted is False
    assert second_id == first_id


def test_queue_transient_failure_retries_then_acks(queue_url: str) -> None:
    queue = PostgresMonitoringQueue(queue_url)
    message_id, _ = queue.enqueue("event:retry", {"event_id": "retry"})
    assert queue.claim(now=NOW) is not None
    status = queue.fail(
        message_id, TimeoutError("database timeout"), transient=True,
        retry_delay=timedelta(seconds=5), now=NOW,
    )
    assert status == "READY"
    assert queue.claim(now=NOW + timedelta(seconds=4)) is None
    retried = queue.claim(now=NOW + timedelta(seconds=5))
    assert retried is not None and retried.attempts == 2
    queue.ack(message_id, now=NOW + timedelta(seconds=5))
    assert _state(queue_url)[0:2] == ("COMPLETED", 2)


def test_queue_permanent_failure_goes_to_dead_letter(queue_url: str) -> None:
    queue = PostgresMonitoringQueue(queue_url)
    message_id, _ = queue.enqueue("event:poison", {"bad": True})
    queue.claim(now=NOW)
    status = queue.fail(
        message_id, ValueError("invalid schema"), transient=False, now=NOW
    )
    assert status == "DEAD_LETTER"
    assert _state(queue_url) == ("DEAD_LETTER", 1, "ValueError", "PERMANENT")
    assert queue.claim(now=NOW + timedelta(days=1)) is None


def test_queue_reclaims_message_after_worker_crash(queue_url: str) -> None:
    queue = PostgresMonitoringQueue(queue_url)
    message_id, _ = queue.enqueue("event:worker-lost", {"event_id": "lost"})
    claimed = queue.claim(now=NOW)
    assert claimed is not None
    assert queue.reclaim_stale(older_than=NOW + timedelta(minutes=1)) == 1
    redelivered = queue.claim(now=NOW + timedelta(minutes=1))
    assert redelivered is not None
    assert redelivered.message_id == message_id
    assert redelivered.attempts == 2
    queue.ack(message_id, now=NOW + timedelta(minutes=1))
