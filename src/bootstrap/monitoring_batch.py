"""Run one deterministic Mongo snapshot batch into PostgreSQL monitoring tables."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo import MongoClient

from monitoring.adapters.mongo import MongoCanonicalBatchSource
from monitoring.adapters.postgres import PostgresEventUnitOfWork
from monitoring.application import ProcessCanonicalEvents
from monitoring.domain import WatermarkPolicy, WindowAggregator
from shared_kernel import Region


def _window_sizes(value: str) -> tuple[int, ...]:
    try:
        sizes = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as error:
        raise argparse.ArgumentTypeError("window sizes must be comma-separated integers") from error
    if not sizes or len(set(sizes)) != len(sizes) or set(sizes) - {1, 5, 15}:
        raise argparse.ArgumentTypeError("window sizes must be unique values from 1,5,15")
    return sizes


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Process one generated Mongo batch into monitoring aggregates"
    )
    parser.add_argument(
        "--seed", type=int, default=int(os.getenv("GENERATOR_SEED", "20260923"))
    )
    parser.add_argument(
        "--window-sizes",
        type=_window_sizes,
        default=_window_sizes(os.getenv("MONITORING_WINDOW_SIZES", "1,5,15")),
    )
    parser.add_argument(
        "--replays", type=int, default=int(os.getenv("MONITORING_REPLAYS", "1"))
    )
    parser.add_argument(
        "--allowed-lateness-minutes",
        type=int,
        default=int(os.getenv("MONITORING_ALLOWED_LATENESS_MINUTES", "5")),
    )
    parser.add_argument(
        "--retention-hours",
        type=int,
        default=int(os.getenv("MONITORING_RETENTION_HOURS", "24")),
    )
    parser.add_argument(
        "--expected-per-region",
        type=int,
        default=int(os.getenv("RECORDS_PER_REGION", "5000")),
    )
    parser.add_argument(
        "--mongo-uri", default=os.getenv("MONGO_URI", "mongodb://localhost:27017")
    )
    parser.add_argument(
        "--database-url",
        default=os.getenv(
            "DATABASE_URL",
            "postgresql://paymenthub:paymenthub@localhost:5432/paymenthub",
        ),
    )
    return parser


def _region_counts(result: Any) -> dict[str, int]:
    return {
        region.value: result.documents_by_region.get(region, 0)
        for region in (Region.VN, Region.ASIA, Region.INDIA)
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.replays <= 0:
        raise SystemExit("--replays must be positive")
    if args.expected_per_region <= 0:
        raise SystemExit("--expected-per-region must be positive")
    if args.allowed_lateness_minutes < 0 or args.retention_hours <= 0:
        raise SystemExit("watermark durations must be positive")
    if args.retention_hours * 60 <= args.allowed_lateness_minutes:
        raise SystemExit("retention must exceed allowed lateness")

    batch_id = f"seed-{args.seed}"
    runs: list[dict[str, Any]] = []
    exit_code = 0
    with MongoClient(
        args.mongo_uri, tz_aware=True, serverSelectionTimeoutMS=10_000
    ) as client:
        client.admin.command("ping")
        source = MongoCanonicalBatchSource(client)
        for replay in range(1, args.replays + 1):
            batch = source.read(batch_id)
            counts = _region_counts(batch)
            count_mismatches = {
                region: {"actual": count, "expected": args.expected_per_region}
                for region, count in counts.items()
                if count != args.expected_per_region
            }
            run: dict[str, Any] = {
                "replay": replay,
                "batch_id": batch_id,
                "read": batch.read,
                "documents_by_region": counts,
                "mapped": len(batch.events),
                "mapping_failed": len(batch.failures),
                "count_mismatches": count_mismatches,
                "mapping_errors": [asdict(error) for error in batch.failures[:20]],
            }
            try:
                output = ProcessCanonicalEvents(
                    PostgresEventUnitOfWork(
                        args.database_url,
                        source_name=f"mongo:{batch_id}",
                        business_date=datetime.strptime(
                            str(args.seed), "%Y%m%d"
                        ).date(),
                    ),
                    WindowAggregator(args.window_sizes),
                    WatermarkPolicy(
                        allowed_lateness=timedelta(
                            minutes=args.allowed_lateness_minutes
                        ),
                        retention=timedelta(hours=args.retention_hours),
                    ),
                ).execute(batch.events, computed_at=datetime.now(UTC))
            except Exception as error:
                run.update(
                    {
                        "status": "FAILED",
                        "error_code": "DATABASE_PROCESSING",
                        "error_type": type(error).__name__,
                        "error_message": str(error)[:500],
                    }
                )
                runs.append(run)
                exit_code = 1
                break

            processing = asdict(output.summary)
            run.update(
                {
                    "inserted": processing["inserted"],
                    "duplicated": processing["duplicated"],
                    "filtered": processing["filtered"],
                    "persistence_failed": processing["failed"],
                    "late": processing["late"],
                    "expired": processing["expired"],
                    "failed": processing["failed"] + len(batch.failures),
                }
            )
            run["aggregate_count"] = len(output.aggregates)
            run["status"] = (
                "COMPLETED"
                if not batch.failures and not count_mismatches and not processing["failed"]
                else "FAILED"
            )
            if run["status"] == "FAILED":
                exit_code = 1
            runs.append(run)

    print(
        json.dumps(
            {
                "batch_id": batch_id,
                "window_sizes": list(args.window_sizes),
                "runs": runs,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
