"""Measure monitoring API p95 while RabbitMQ heartbeat ingest remains active."""

from __future__ import annotations

import asyncio
import json
import math
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import aio_pika
import httpx
from aio_pika import DeliveryMode, Message

from monitoring.adapters.rabbitmq import MAIN_EXCHANGE, MAIN_ROUTING_KEY


OUTPUT = Path(__file__).with_name("result-w5.json")
ENDPOINTS = (
    "/health/providers?limit=500",
    "/metrics/windows?window_size=5&limit=500",
    "/alerts?status=OPEN&limit=200",
    "/_meta/freshness?limit=500",
)


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        raise ValueError("at least one latency sample is required")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile_value * len(ordered)) - 1)]


async def _produce(url: str, count: int, delay: float, state: dict[str, int]) -> None:
    connection = await aio_pika.connect_robust(url)
    async with connection:
        channel = await connection.channel(publisher_confirms=True)
        exchange = await channel.get_exchange(MAIN_EXCHANGE)
        for index in range(count):
            sent_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            body = {
                "schema_version": 1,
                "message_type": "heartbeat",
                "source_name": "w5-api-benchmark",
                "region": "VN",
                "tenant_id": None,
                "provider": "W5_BENCH",
                "sent_at": sent_at,
            }
            await exchange.publish(
                Message(
                    json.dumps(body).encode(),
                    content_type="application/json",
                    delivery_mode=DeliveryMode.PERSISTENT,
                    message_id=f"w5-heartbeat-{index}",
                ),
                routing_key=MAIN_ROUTING_KEY,
            )
            state["confirmed"] += 1
            await asyncio.sleep(delay)


async def benchmark(
    *, api_url: str, rabbitmq_url: str, message_count: int, publish_delay: float
) -> dict[str, object]:
    state = {"confirmed": 0}
    samples = {endpoint: [] for endpoint in ENDPOINTS}
    failures: list[dict[str, object]] = []
    producer = asyncio.create_task(
        _produce(rabbitmq_url, message_count, publish_delay, state)
    )
    async with httpx.AsyncClient(base_url=api_url, timeout=10) as client:
        while not producer.done():
            for endpoint in ENDPOINTS:
                started = time.perf_counter_ns()
                response = await client.get(endpoint)
                elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
                samples[endpoint].append(elapsed_ms)
                if response.status_code != 200:
                    failures.append({"endpoint": endpoint, "status": response.status_code})
                if producer.done():
                    break
        await producer
    all_samples = [value for endpoint in ENDPOINTS for value in samples[endpoint]]
    return {
        "schema_version": 1,
        "benchmark": "p1-w5-api-under-active-rabbitmq-ingest",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "load": {
            "transport": "RabbitMQ publisher confirms",
            "messages_confirmed": state["confirmed"],
            "publish_delay_seconds": publish_delay,
            "requests_while_producer_active": len(all_samples),
        },
        "api": {
            "request_count": len(all_samples),
            "failures": failures,
            "p95_ms": round(percentile(all_samples, 0.95), 3),
            "per_endpoint": {
                endpoint.split("?", 1)[0]: {
                    "requests": len(values),
                    "p95_ms": round(percentile(values, 0.95), 3),
                }
                for endpoint, values in samples.items()
            },
        },
        "pass": state["confirmed"] == message_count and not failures and bool(all_samples),
    }


def main() -> int:
    result = asyncio.run(
        benchmark(
            api_url=os.getenv("MONITORING_API_URL", "http://localhost:8000"),
            rabbitmq_url=os.getenv(
                "RABBITMQ_URL", "amqp://paymenthub:paymenthub@localhost:5672/"
            ),
            message_count=int(os.getenv("W5_BENCH_MESSAGES", "100")),
            publish_delay=float(os.getenv("W5_BENCH_PUBLISH_DELAY_SECONDS", "0.02")),
        )
    )
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
