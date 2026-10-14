"""Producer and verifier for the W4 kill-during-ingest benchmark.

The orchestrating commands live in docs/testing-guide.md because killing a
container requires Docker host access. Both modes run inside the dedicated W4
Compose network and never reset a database themselves.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aio_pika
import psycopg
from aio_pika import DeliveryMode, Message


OUTPUT = Path(__file__).with_name("result-w4.json")
READY_MARKER = Path(__file__).with_name(".w4-kill-ready")
RESUME_MARKER = Path(__file__).with_name(".w4-kill-resume")
TOTAL = 25
KILL_AFTER = TOTAL * 40 // 100
BASE = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)


def _payload(index: int) -> dict[str, object]:
    event_time = BASE + timedelta(seconds=index)
    return {
        "schema_version": 1,
        "event_id": f"w4-kill-{index:03d}",
        "region": "VN",
        "tenant_id": None,
        "txn_id": f"w4-kill-{index:03d}",
        "txn_type": "PAYMENT",
        "instrument": "CARD",
        "channel_raw": "card",
        "provider": "W4_KILL_BENCH",
        "status": "SUCCESS",
        "amount_status": "OK",
        "amount_minor": 100 + index,
        "currency": "VND",
        "created_at": event_time.isoformat().replace("+00:00", "Z"),
        "updated_at": event_time.isoformat().replace("+00:00", "Z"),
        "is_terminal": True,
        "is_money_movement": True,
        "is_reconcilable": True,
    }


async def produce(*, replay: bool = False) -> int:
    pause_at_kill = os.getenv("W4_PAUSE_AT_KILL", "0") == "1" and not replay
    if pause_at_kill:
        READY_MARKER.unlink(missing_ok=True)
        RESUME_MARKER.unlink(missing_ok=True)
    connection = await aio_pika.connect_robust(os.environ["RABBITMQ_URL"])
    async with connection:
        channel = await connection.channel(publisher_confirms=True)
        exchange = await channel.get_exchange("payment.events")
        for index in range(1, TOTAL + 1):
            payload = _payload(index)
            await exchange.publish(
                Message(
                    json.dumps(payload).encode(),
                    content_type="application/json",
                    delivery_mode=DeliveryMode.PERSISTENT,
                    message_id=payload["event_id"],
                    headers={"x-w4-replay": replay},
                ),
                routing_key="monitoring.canonical",
            )
            print(
                json.dumps(
                    {
                        "publisher_confirmed": index,
                        "event_id": payload["event_id"],
                        "replay": replay,
                    }
                ),
                flush=True,
            )
            if pause_at_kill and index == KILL_AFTER:
                READY_MARKER.write_text("publisher-confirmed-40-percent\n")
                print(json.dumps({"waiting_for_kill": True}), flush=True)
                while not RESUME_MARKER.exists():
                    await asyncio.sleep(0.1)
            await asyncio.sleep(float(os.getenv("W4_PUBLISH_DELAY_SECONDS", "0.15")))
    return 0


def verify() -> int:
    expected = int(os.getenv("W4_EXPECTED_CONFIRMED", str(TOTAL)))
    deadline = time.monotonic() + float(os.getenv("W4_DRAIN_TIMEOUT_SECONDS", "60"))
    row = (0, 0, 0)
    while time.monotonic() < deadline:
        with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
            row = connection.execute(
                """SELECT count(*), count(DISTINCT event_id),
                          count(*) FILTER (WHERE job_run_id IS NULL)
                   FROM canonical_event
                   WHERE txn_id LIKE 'w4-kill-%'"""
            ).fetchone()
        if row[1] == expected:
            break
        time.sleep(0.25)

    canonical_rows, distinct_event_ids, null_lineage = row
    result = {
        "schema_version": 2,
        "benchmark": "p1-w4-rabbitmq-kill-during-ingest",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "publisher_confirmed": expected,
        "kill_after_confirmed": KILL_AFTER,
        "kill_percent": 40,
        "container_killed": "rabbitmq-consumer",
        "canonical_rows": canonical_rows,
        "distinct_event_ids": distinct_event_ids,
        "lost": max(0, expected - distinct_event_ids),
        "duplicated": max(0, canonical_rows - distinct_event_ids),
        "null_job_run_id_rows": null_lineage,
        "pass": (
            canonical_rows == expected
            and distinct_event_ids == expected
            and null_lineage == 0
        ),
    }
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["pass"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("produce", "replay", "verify"))
    args = parser.parse_args()
    if args.mode in {"produce", "replay"}:
        return asyncio.run(produce(replay=args.mode == "replay"))
    return verify()


if __name__ == "__main__":
    raise SystemExit(main())
