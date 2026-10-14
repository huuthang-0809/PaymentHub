"""Consume canonical events with an ACK-after-database-commit boundary."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import psycopg

from monitoring.adapters.postgres import PostgresEventUnitOfWork
from monitoring.adapters.rabbitmq import (
    MAIN_QUEUE,
    RabbitMQMessageHandler,
    SourceHeartbeat,
    connect,
    decode_canonical_event,
    declare_topology,
)
from monitoring.application import ProcessCanonicalEvents
from monitoring.domain import CanonicalEvent, WatermarkPolicy, WindowAggregator


def _database_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://paymenthub:paymenthub@localhost:5432/paymenthub",
    ).replace("postgresql+psycopg://", "postgresql://", 1)


def _processor(events: Sequence[CanonicalEvent]) -> None:
    if not events:
        return
    ProcessCanonicalEvents(
        PostgresEventUnitOfWork(
            _database_url(),
            source_name="rabbitmq:canonical-events",
            business_date=max(event.event_time for event in events).date(),
            aggregate_from=datetime.now(UTC) - timedelta(
                hours=int(os.getenv("MONITORING_REALTIME_LOOKBACK_HOURS", "24"))
            ),
        ),
        WindowAggregator(
            tuple(
                int(value.strip())
                for value in os.getenv("MONITORING_WINDOW_SIZES", "1,5,15").split(",")
                if value.strip()
            )
        ),
        WatermarkPolicy(
            allowed_lateness=timedelta(
                minutes=int(os.getenv("MONITORING_ALLOWED_LATENESS_MINUTES", "5"))
            ),
            retention=timedelta(
                hours=int(os.getenv("MONITORING_RETENTION_HOURS", "24"))
            ),
        ),
    ).execute(events, computed_at=datetime.now(UTC), fail_fast=True)


def _record_heartbeat(heartbeat: SourceHeartbeat) -> None:
    # This transaction completes before RabbitMQ receives the ACK.
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            INSERT INTO source_activity (
                source_name, region, tenant_scope, provider,
                last_message_received_at, last_heartbeat_at
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_name, region, tenant_scope, provider) DO UPDATE SET
                last_message_received_at = GREATEST(
                    source_activity.last_message_received_at,
                    EXCLUDED.last_message_received_at
                ),
                last_heartbeat_at = GREATEST(
                    source_activity.last_heartbeat_at,
                    EXCLUDED.last_heartbeat_at
                ),
                updated_at = now()
            """,
            (
                heartbeat.source_name, heartbeat.region.value,
                heartbeat.tenant_scope, heartbeat.provider,
                heartbeat.received_at, heartbeat.sent_at,
            ),
        )


async def async_main() -> None:
    connection = await connect(
        os.getenv(
            "RABBITMQ_URL", "amqp://paymenthub:paymenthub@localhost:5672/"
        )
    )
    async with connection:
        channel = await connection.channel(publisher_confirms=True)
        batch_size = int(os.getenv("RABBITMQ_BATCH_SIZE", "20"))
        batch_wait_seconds = float(os.getenv("RABBITMQ_BATCH_WAIT_SECONDS", "5"))
        if batch_size <= 0 or batch_wait_seconds < 0:
            raise ValueError("RabbitMQ batch size must be positive and wait non-negative")
        await channel.set_qos(
            prefetch_count=max(
                batch_size,
                int(os.getenv("RABBITMQ_PREFETCH", str(batch_size))),
            )
        )
        await declare_topology(channel)
        transient_errors = (
            OSError,
            TimeoutError,
            psycopg.OperationalError,
            psycopg.InterfaceError,
        )
        queue = await channel.get_queue(MAIN_QUEUE)
        inbox: asyncio.Queue = asyncio.Queue(maxsize=batch_size * 2)

        async def enqueue(message) -> None:
            await inbox.put(message)

        await queue.consume(enqueue, no_ack=False)
        print(
            f"RabbitMQ consumer ready: queue={MAIN_QUEUE}, "
            f"batch_size={batch_size}, batch_wait={batch_wait_seconds}s",
            flush=True,
        )

        while True:
            messages = [await inbox.get()]
            deadline = asyncio.get_running_loop().time() + batch_wait_seconds
            while len(messages) < batch_size:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    messages.append(
                        await asyncio.wait_for(inbox.get(), timeout=remaining)
                    )
                except TimeoutError:
                    break

            decoded: list[tuple[object, CanonicalEvent | SourceHeartbeat]] = []
            invalid_handler = RabbitMQMessageHandler(
                channel,
                lambda _message: None,
                transient_errors=transient_errors,
            )
            for message in messages:
                try:
                    decoded.append((message, decode_canonical_event(message.body)))
                except Exception:
                    # Reuse the standard permanent-error and DLQ policy.
                    await invalid_handler(message)

            events = [
                item for _, item in decoded if isinstance(item, CanonicalEvent)
            ]
            heartbeats = [
                item for _, item in decoded if isinstance(item, SourceHeartbeat)
            ]
            try:
                await asyncio.to_thread(_processor, events)
                for heartbeat in heartbeats:
                    await asyncio.to_thread(_record_heartbeat, heartbeat)
            except Exception as error:
                def fail(_message, *, captured=error) -> None:
                    raise captured

                failure_handler = RabbitMQMessageHandler(
                    channel,
                    fail,
                    transient_errors=transient_errors,
                )
                for message, _ in decoded:
                    await failure_handler(message)
            else:
                for message, _ in decoded:
                    await message.ack()
                print(
                    f"RabbitMQ batch committed: messages={len(decoded)}, "
                    f"events={len(events)}, heartbeats={len(heartbeats)}",
                    flush=True,
                )


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
