"""RabbitMQ + PostgreSQL acceptance tests for the Week 4 ingest contract.

These tests are destructive to TEST_DATABASE_URL and must use the dedicated
Week 4 Compose project documented in docs/testing-guide.md.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import aio_pika
import psycopg
import pytest
from aio_pika import DeliveryMode, Message
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from monitoring.adapters.postgres import PostgresEventUnitOfWork
from monitoring.adapters.rabbitmq import (
    DLQ_QUEUE,
    DLQ_ROUTING_KEY,
    DLX_EXCHANGE,
    MAIN_EXCHANGE,
    MAIN_QUEUE,
    MAIN_ROUTING_KEY,
    RabbitMQMessageHandler,
    connect,
    declare_topology,
    reprocess_dlq_message,
)
from monitoring.application import ProcessCanonicalEvents
from monitoring.domain import CanonicalEvent, WatermarkPolicy, WindowAggregator


pytestmark = pytest.mark.integration
BASE = datetime(2026, 10, 11, 4, 0, tzinfo=UTC)


def _settings() -> tuple[str, str]:
    if os.getenv("PAYMENTHUB_RUN_RABBITMQ_INTEGRATION") != "1":
        pytest.skip("set PAYMENTHUB_RUN_RABBITMQ_INTEGRATION=1")
    database_url = os.getenv("TEST_DATABASE_URL")
    rabbitmq_url = os.getenv("RABBITMQ_TEST_URL")
    if not database_url or not rabbitmq_url:
        pytest.skip("TEST_DATABASE_URL and RABBITMQ_TEST_URL are required")
    return database_url, rabbitmq_url


def _psycopg_url(value: str) -> str:
    parsed = make_url(value)
    return parsed.set(drivername="postgresql").render_as_string(hide_password=False)


@pytest.fixture(scope="module")
def services() -> tuple[str, str]:
    database_url, rabbitmq_url = _settings()
    with psycopg.connect(_psycopg_url(database_url), autocommit=True) as connection:
        connection.execute("DROP SCHEMA IF EXISTS public CASCADE")
        connection.execute("CREATE SCHEMA public")
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    try:
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
    return database_url, rabbitmq_url


def _payload(name: str, event_time: datetime, **changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "event_id": f"w4-{name}",
        "region": "VN",
        "tenant_id": None,
        "txn_id": f"w4-{name}",
        "txn_type": "PAYMENT",
        "instrument": "CARD",
        "channel_raw": "card",
        "provider": f"W4_{name.upper()}",
        "status": "SUCCESS",
        "amount_status": "OK",
        "amount_minor": 100,
        "currency": "VND",
        "created_at": event_time.isoformat().replace("+00:00", "Z"),
        "updated_at": event_time.isoformat().replace("+00:00", "Z"),
        "is_terminal": True,
        "is_money_movement": True,
        "is_reconcilable": True,
    }
    value.update(changes)
    return value


async def _channel(url: str):
    connection = await connect(url)
    channel = await connection.channel(publisher_confirms=True)
    await declare_topology(channel)
    return connection, channel


async def _publish(channel, payload: dict[str, object], *, message_id: str) -> None:
    exchange = await channel.get_exchange(MAIN_EXCHANGE)
    await exchange.publish(
        Message(
            json.dumps(payload).encode(),
            content_type="application/json",
            delivery_mode=DeliveryMode.PERSISTENT,
            message_id=message_id,
        ),
        routing_key=MAIN_ROUTING_KEY,
    )


def _handler(channel, database_url: str, source_name: str, *, fail=None):
    attempts = 0

    async def process(event: CanonicalEvent) -> None:
        nonlocal attempts
        attempts += 1
        if fail is not None:
            fail(attempts)
        ProcessCanonicalEvents(
            PostgresEventUnitOfWork(database_url, source_name=source_name),
            WindowAggregator((5,)),
            WatermarkPolicy(
                allowed_lateness=timedelta(minutes=5),
                retention=timedelta(hours=24),
            ),
        ).execute([event], computed_at=BASE + timedelta(days=1), fail_fast=True)

    return RabbitMQMessageHandler(
        channel, process, transient_errors=(TimeoutError, psycopg.OperationalError)
    )


async def _get(queue, *, timeout: float = 8.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        message = await queue.get(fail=False, no_ack=False)
        if message is not None:
            return message
        await asyncio.sleep(0.1)
    raise AssertionError(f"message did not arrive on {queue.name}")


def _count(database_url: str, txn_id: str) -> int:
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        return connection.execute(
            "SELECT count(*) FROM canonical_event WHERE txn_id = %s", (txn_id,)
        ).fetchone()[0]


def test_duplicate_delivery_is_idempotent(services: tuple[str, str]) -> None:
    database_url, rabbitmq_url = services

    async def scenario() -> None:
        connection, channel = await _channel(rabbitmq_url)
        async with connection:
            queue = await channel.get_queue(MAIN_QUEUE)
            await queue.purge()
            payload = _payload("duplicate", BASE)
            await _publish(channel, payload, message_id="duplicate-1")
            await _publish(channel, payload, message_id="duplicate-2")
            handler = _handler(channel, database_url, "rabbit-it-duplicate")
            await handler(await _get(queue))
            await handler(await _get(queue))

    asyncio.run(scenario())
    assert _count(database_url, "w4-duplicate") == 1


def test_out_of_order_delivery_produces_stable_aggregate(
    services: tuple[str, str],
) -> None:
    database_url, rabbitmq_url = services

    async def scenario() -> None:
        connection, channel = await _channel(rabbitmq_url)
        async with connection:
            queue = await channel.get_queue(MAIN_QUEUE)
            await queue.purge()
            later = _payload("order-later", BASE + timedelta(hours=1, minutes=2), provider="W4_ORDER")
            earlier = _payload("order-earlier", BASE + timedelta(hours=1), provider="W4_ORDER")
            handler = _handler(channel, database_url, "rabbit-it-order")
            await _publish(channel, later, message_id="order-later")
            await handler(await _get(queue))
            await _publish(channel, earlier, message_id="order-earlier")
            await handler(await _get(queue))

    asyncio.run(scenario())
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        row = connection.execute(
            """SELECT total_count, late_event_count FROM window_aggregate
               WHERE provider = 'W4_ORDER' AND window_size_minutes = 5"""
        ).fetchone()
    assert row == (2, 0)


def test_late_event_updates_closed_window_and_counter(
    services: tuple[str, str],
) -> None:
    database_url, rabbitmq_url = services

    async def scenario() -> None:
        connection, channel = await _channel(rabbitmq_url)
        async with connection:
            queue = await channel.get_queue(MAIN_QUEUE)
            await queue.purge()
            handler = _handler(channel, database_url, "rabbit-it-late")
            anchor = _payload("late-anchor", BASE + timedelta(hours=2), provider="W4_LATE")
            late = _payload("late-event", BASE + timedelta(hours=1, minutes=50), provider="W4_LATE")
            await _publish(channel, anchor, message_id="late-anchor")
            await handler(await _get(queue))
            await _publish(channel, late, message_id="late-event")
            await handler(await _get(queue))

    asyncio.run(scenario())
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        row = connection.execute(
            """SELECT sum(total_count), sum(late_event_count)
               FROM window_aggregate WHERE provider = 'W4_LATE'"""
        ).fetchone()
    assert row == (2, 1)


def test_transient_failure_returns_through_retry_queue(
    services: tuple[str, str],
) -> None:
    database_url, rabbitmq_url = services

    async def scenario() -> None:
        connection, channel = await _channel(rabbitmq_url)
        async with connection:
            queue = await channel.get_queue(MAIN_QUEUE)
            await queue.purge()
            payload = _payload("retry", BASE + timedelta(hours=3))

            def fail_first(attempt: int) -> None:
                if attempt == 1:
                    raise TimeoutError("temporary database timeout")

            handler = _handler(
                channel, database_url, "rabbit-it-retry", fail=fail_first
            )
            await _publish(channel, payload, message_id="retry")
            result = await handler(await _get(queue))
            assert result.disposition == "RETRY" and result.retry_count == 1
            retried = await _get(queue)
            assert retried.headers["x-retry-count"] == 1
            assert (await handler(retried)).disposition == "ACK"

    asyncio.run(scenario())
    assert _count(database_url, "w4-retry") == 1


def test_dlq_masks_raw_and_corrected_reprocess_is_idempotent(
    services: tuple[str, str],
) -> None:
    database_url, rabbitmq_url = services

    async def scenario() -> None:
        connection, channel = await _channel(rabbitmq_url)
        async with connection:
            main = await channel.get_queue(MAIN_QUEUE)
            dlq = await channel.get_queue(DLQ_QUEUE)
            await main.purge()
            await dlq.purge()
            handler = _handler(channel, database_url, "rabbit-it-dlq")
            invalid = {"event_id": "w4-dlq-invalid", "password": "top-secret"}
            await _publish(channel, invalid, message_id="raw-reference-1")
            assert (await handler(await _get(main))).disposition == "DEAD_LETTER"
            dead = await _get(dlq)
            envelope = json.loads(dead.body)
            assert envelope["error_code"] == "MISSING_FIELD"
            assert envelope["raw_reference"] == "raw-reference-1"
            assert envelope["payload"]["password"] == "***MASKED***"
            await dead.ack()

            corrected = _payload("dlq-corrected", BASE + timedelta(hours=4))
            dlx = await channel.get_exchange(DLX_EXCHANGE)
            for suffix in ("a", "b"):
                body = json.dumps({"error_code": "CORRECTED", "payload": corrected}).encode()
                await dlx.publish(
                    Message(body, message_id=f"corrected-{suffix}"),
                    routing_key=DLQ_ROUTING_KEY,
                )
                await reprocess_dlq_message(channel, await _get(dlq))
            await handler(await _get(main))
            await handler(await _get(main))

    asyncio.run(scenario())
    assert _count(database_url, "w4-dlq-corrected") == 1
