"""Continuously publish realistic transaction lifecycles to RabbitMQ.

This is a demo/load tool. It deliberately enters the system through the same
durable exchange as a real canonical-event producer; it never writes Grafana
or PostgreSQL directly.
"""

from __future__ import annotations

import asyncio
import heapq
import json
import os
import random
import signal
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import count
from time import monotonic
from typing import Any

from aio_pika import DeliveryMode, Message

from monitoring.adapters.rabbitmq import (
    MAIN_EXCHANGE,
    MAIN_ROUTING_KEY,
    connect,
    declare_topology,
)


@dataclass(frozen=True, slots=True)
class Scope:
    region: str
    tenant_id: str | None
    provider: str
    currency: str


SCOPES = (
    Scope("VN", None, "ONEPAY", "VND"),
    Scope("ASIA", "asia-id", "ESPAY", "IDR"),
    Scope("ASIA", "asia-ph", "2C2P", "PHP"),
    Scope("INDIA", None, "RAZORPAY", "INR"),
)
CHANNELS = (
    ("api", "CARD"),
    ("mobile", "EWALLET"),
    ("web", "PAYMENT_LINK"),
    ("qr", "QR"),
)


@dataclass(frozen=True, slots=True)
class ScheduledPayload:
    delay_seconds: float
    payload: dict[str, Any]


class ScenarioGenerator:
    """Build deterministic, varied transaction lifecycles from one RNG."""

    def __init__(
        self,
        *,
        seed: int,
        failure_rate: float,
        stuck_rate: float,
        min_terminal_delay: float,
        max_terminal_delay: float,
    ) -> None:
        if not 0 <= failure_rate <= 1 or not 0 <= stuck_rate <= 1:
            raise ValueError("failure_rate and stuck_rate must be between 0 and 1")
        if min_terminal_delay <= 0 or max_terminal_delay < min_terminal_delay:
            raise ValueError("terminal delay range is invalid")
        self.random = random.Random(seed)
        self.failure_rate = failure_rate
        self.stuck_rate = stuck_rate
        self.min_terminal_delay = min_terminal_delay
        self.max_terminal_delay = max_terminal_delay

    def transaction(self, sequence: int, created_at: datetime) -> list[ScheduledPayload]:
        scope = self.random.choice(SCOPES)
        channel, instrument = self.random.choice(CHANNELS)
        txn_type = self.random.choices(
            ("PAYMENT", "LOAD", "REFUND"), weights=(80, 15, 5), k=1
        )[0]
        prefix = created_at.strftime("%Y%m%d%H%M%S")
        txn_id = f"rt-{prefix}-{sequence:09d}"
        amount_minor = self.random.randint(100, 250_000) * 100
        terminal_delay = self.random.uniform(
            self.min_terminal_delay, self.max_terminal_delay
        )
        pending_delay = min(1.0, terminal_delay / 3)

        def payload(status: str, delay: float, *, terminal: bool) -> dict[str, Any]:
            event_time = created_at + timedelta(seconds=delay)
            return {
                "schema_version": 1,
                "event_id": f"{txn_id}:{status.lower()}",
                "region": scope.region,
                "tenant_id": scope.tenant_id,
                "txn_id": txn_id,
                "txn_type": txn_type,
                "instrument": instrument,
                "channel_raw": channel,
                "provider": scope.provider,
                "status": status,
                "amount_status": "OK",
                "amount_minor": amount_minor,
                "currency": scope.currency,
                "created_at": _iso(created_at),
                "updated_at": _iso(event_time),
                "is_terminal": terminal,
                "is_money_movement": True,
                "is_reconcilable": True,
                "is_deleted": False,
                "is_test_data": False,
                "raw": {"simulator": "paymenthub-realtime-v1"},
            }

        events = [
            ScheduledPayload(0, payload("CREATED", 0, terminal=False)),
            ScheduledPayload(
                pending_delay,
                payload("PENDING", pending_delay, terminal=False),
            ),
        ]
        if self.random.random() >= self.stuck_rate:
            terminal_status = (
                "FAILED" if self.random.random() < self.failure_rate else "SUCCESS"
            )
            events.append(
                ScheduledPayload(
                    terminal_delay,
                    payload(terminal_status, terminal_delay, terminal=True),
                )
            )
        return events


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _float_env(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


async def run() -> None:
    transactions_per_second = _float_env("REALTIME_TXNS_PER_SECOND", 0.2)
    if transactions_per_second <= 0:
        raise ValueError("REALTIME_TXNS_PER_SECOND must be greater than zero")
    generator = ScenarioGenerator(
        seed=int(os.getenv("REALTIME_SEED", "20261013")),
        failure_rate=_float_env("REALTIME_FAILURE_RATE", 0.03),
        stuck_rate=_float_env("REALTIME_STUCK_RATE", 0.01),
        min_terminal_delay=_float_env("REALTIME_MIN_TERMINAL_DELAY_SECONDS", 2),
        max_terminal_delay=_float_env("REALTIME_MAX_TERMINAL_DELAY_SECONDS", 12),
    )
    connection = await connect(
        os.getenv(
            "RABBITMQ_URL",
            "amqp://paymenthub:paymenthub@localhost:5672/",
        )
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            loop.add_signal_handler(getattr(signal, name), stop.set)

    scheduled: list[tuple[float, int, dict[str, Any]]] = []
    tie_breaker = count()
    sequence = count(1)
    interval = 1 / transactions_per_second
    next_transaction = monotonic()
    published = 0

    async with connection:
        channel = await connection.channel(publisher_confirms=True)
        await declare_topology(channel)
        exchange = await channel.get_exchange(MAIN_EXCHANGE)
        print(
            "Realtime simulator ready: "
            f"rate={transactions_per_second} txn/s, "
            f"failure_rate={generator.failure_rate}, stuck_rate={generator.stuck_rate}",
            flush=True,
        )
        while not stop.is_set():
            now_mono = monotonic()
            if now_mono >= next_transaction:
                created_at = datetime.now(UTC)
                txn_sequence = next(sequence)
                for scheduled_payload in generator.transaction(txn_sequence, created_at):
                    heapq.heappush(
                        scheduled,
                        (
                            now_mono + scheduled_payload.delay_seconds,
                            next(tie_breaker),
                            scheduled_payload.payload,
                        ),
                    )
                next_transaction += interval

            while scheduled and scheduled[0][0] <= monotonic():
                _, _, payload = heapq.heappop(scheduled)
                encoded = json.dumps(payload, separators=(",", ":")).encode()
                await exchange.publish(
                    Message(
                        encoded,
                        content_type="application/json",
                        delivery_mode=DeliveryMode.PERSISTENT,
                        message_id=str(payload["event_id"]),
                        correlation_id=str(payload["txn_id"]),
                    ),
                    routing_key=MAIN_ROUTING_KEY,
                )
                published += 1
                if published % 10 == 0:
                    print(f"Publisher-confirmed events: {published}", flush=True)

            next_due = min(
                next_transaction,
                scheduled[0][0] if scheduled else next_transaction,
            )
            try:
                await asyncio.wait_for(stop.wait(), timeout=max(0.01, next_due - monotonic()))
            except TimeoutError:
                pass

    print(f"Realtime simulator stopped: published={published}", flush=True)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
