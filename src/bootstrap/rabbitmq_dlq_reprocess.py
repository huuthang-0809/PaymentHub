"""Requeue valid DLQ messages. Run explicitly after correcting their payload."""

from __future__ import annotations

import asyncio
import os

from monitoring.adapters.rabbitmq import (
    DLQ_QUEUE,
    connect,
    declare_topology,
    reprocess_dlq_message,
)


async def async_main() -> None:
    connection = await connect(
        os.getenv(
            "RABBITMQ_URL", "amqp://paymenthub:paymenthub@localhost:5672/"
        )
    )
    async with connection:
        channel = await connection.channel(publisher_confirms=True)
        await declare_topology(channel)
        queue = await channel.get_queue(DLQ_QUEUE)
        limit = int(os.getenv("RABBITMQ_DLQ_REPROCESS_LIMIT", "100"))
        processed = 0
        for _ in range(limit):
            message = await queue.get(fail=False, no_ack=False)
            if message is None:
                break
            try:
                await reprocess_dlq_message(channel, message)
            except Exception:
                await message.nack(requeue=True)
                raise
            processed += 1
        print(f"RabbitMQ DLQ messages requeued: {processed}", flush=True)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
