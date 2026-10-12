from __future__ import annotations

import asyncio
import os

from monitoring.adapters.rabbitmq import (
    connect,
    declare_topology,
)


async def async_main() -> None:
    url = os.environ.get(
        "RABBITMQ_URL",
        "amqp://paymenthub:paymenthub@localhost:5672/",
    )

    connection = await connect(url)

    async with connection:
        channel = await connection.channel(
            publisher_confirms=True,
        )
        await declare_topology(channel)

    print("RabbitMQ topology created")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
