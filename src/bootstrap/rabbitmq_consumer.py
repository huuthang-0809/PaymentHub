"""Consume canonical events with an ACK-after-database-commit boundary."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import psycopg

from monitoring.adapters.postgres import PostgresEventUnitOfWork
from monitoring.adapters.rabbitmq import (
    MAIN_QUEUE,
    RabbitMQMessageHandler,
    connect,
    declare_topology,
)
from monitoring.application import ProcessCanonicalEvents
from monitoring.domain import CanonicalEvent, WatermarkPolicy, WindowAggregator


def _database_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://paymenthub:paymenthub@localhost:5432/paymenthub",
    ).replace("postgresql+psycopg://", "postgresql://", 1)


def _processor(event: CanonicalEvent) -> None:
    ProcessCanonicalEvents(
        PostgresEventUnitOfWork(
            _database_url(),
            source_name="rabbitmq:canonical-events",
            business_date=event.event_time.date(),
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
    ).execute([event], computed_at=datetime.now(UTC))


async def async_main() -> None:
    connection = await connect(
        os.getenv(
            "RABBITMQ_URL", "amqp://paymenthub:paymenthub@localhost:5672/"
        )
    )
    async with connection:
        channel = await connection.channel(publisher_confirms=True)
        await channel.set_qos(prefetch_count=int(os.getenv("RABBITMQ_PREFETCH", "10")))
        await declare_topology(channel)

        async def process(event: CanonicalEvent) -> None:
            await asyncio.to_thread(_processor, event)

        handler = RabbitMQMessageHandler(
            channel,
            process,
            transient_errors=(
                OSError,
                TimeoutError,
                psycopg.OperationalError,
                psycopg.InterfaceError,
            ),
        )
        queue = await channel.get_queue(MAIN_QUEUE)
        await queue.consume(handler, no_ack=False)
        print(f"RabbitMQ consumer ready: queue={MAIN_QUEUE}", flush=True)
        await asyncio.Future()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
