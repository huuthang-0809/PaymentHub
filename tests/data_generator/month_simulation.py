"""Generate daily fixtures and run one virtual month through the scheduler."""

import json
import os
import time as clock
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from urllib.request import urlopen

from pymongo import MongoClient

from reconciliation.adapters.daily_scheduler import (
    DailyReconciliationScheduler,
    HttpReconciliationSubmitter,
    load_schedules,
)

from .generator import GeneratorConfig, generate_dataset, generate_provider_statements
from .mongo_seed import _write_documents


DEFAULT_SCHEDULES = json.dumps([
    {
        "region": "VN",
        "provider": "ONEPAY",
        "business_timezone": "Asia/Ho_Chi_Minh",
        "cutoff_time": "02:00",
        "run_time": "03:00",
        "filename": "onepay_{business_date_compact}.csv",
    },
    {
        "region": "ASIA",
        "tenant_id": "asia-id",
        "provider": "ESPAY",
        "business_timezone": "Asia/Jakarta",
        "cutoff_time": "02:00",
        "run_time": "03:00",
        "filename": "espay_asia-id_{business_date_compact}.csv",
    },
    {
        "region": "ASIA",
        "tenant_id": "asia-ph",
        "provider": "2C2P",
        "business_timezone": "Asia/Manila",
        "cutoff_time": "02:00",
        "run_time": "03:00",
        "filename": "2c2p_asia-ph_{business_date_compact}.csv",
    },
    {
        "region": "INDIA",
        "provider": "RAZORPAY",
        "business_timezone": "Asia/Kolkata",
        "cutoff_time": "02:00",
        "run_time": "03:00",
        "filename": "razorpay_{business_date_compact}.csv",
    },
])


def simulation_dates(start: date, days: int) -> tuple[date, ...]:
    if days <= 0:
        raise ValueError("SIMULATION_DAYS must be positive")
    return tuple(start + timedelta(days=offset) for offset in range(days))


def missing_statement_targets(
    dates: tuple[date, ...], count: int
) -> dict[date, int]:
    """Pick evenly spaced virtual days and the schedule missing on each day."""
    if count < 0 or count > 2:
        raise ValueError("SIMULATION_MISSING_STATEMENT_COUNT must be between 0 and 2")
    if count > len(dates):
        raise ValueError(
            "SIMULATION_MISSING_STATEMENT_COUNT must not exceed SIMULATION_DAYS"
        )
    if count == 0:
        return {}

    day_indexes = [
        ((index + 1) * (len(dates) + 1) // (count + 1)) - 1
        for index in range(count)
    ]
    return {
        dates[day_index]: schedule_index
        for schedule_index, day_index in enumerate(day_indexes)
    }


def _wait_for_api(api_url: str) -> None:
    health_url = api_url.rstrip("/") + "/health"
    for _ in range(60):
        try:
            with urlopen(health_url, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            clock.sleep(1)
    raise RuntimeError(f"API did not become ready: {health_url}")


def _emit(event: str, context: dict[str, object]) -> None:
    print(json.dumps({"event": event, **context}, separators=(",", ":")), flush=True)


def main() -> None:
    start = date.fromisoformat(os.getenv("SIMULATION_START_DATE", "2026-09-01"))
    days = int(os.getenv("SIMULATION_DAYS", "30"))
    seconds_per_day = float(os.getenv("SIMULATION_SECONDS_PER_DAY", "2"))
    if seconds_per_day < 0:
        raise ValueError("SIMULATION_SECONDS_PER_DAY must not be negative")
    missing_statement_count = int(
        os.getenv("SIMULATION_MISSING_STATEMENT_COUNT", "2")
    )

    records = int(os.getenv("RECORDS_PER_REGION", "100"))
    mongo_uri = os.getenv("MONGO_URI", "mongodb://mongodb:27017")
    api_url = os.getenv("RECON_API_URL", "http://api:8000")
    output = Path(os.getenv("SIMULATION_OUTPUT_DIR", "/test-data/month-simulation"))
    statements_dir = output / "provider-statements"
    truth_dir = output / "ground-truth"
    statements_dir.mkdir(parents=True, exist_ok=True)
    truth_dir.mkdir(parents=True, exist_ok=True)

    _wait_for_api(api_url)
    scheduler = DailyReconciliationScheduler(
        statements_dir,
        load_schedules(DEFAULT_SCHEDULES),
        HttpReconciliationSubmitter(api_url, timeout_seconds=120),
        _emit,
    )
    dates = simulation_dates(start, days)
    missing_targets = missing_statement_targets(dates, missing_statement_count)

    with MongoClient(mongo_uri, serverSelectionTimeoutMS=10_000) as client:
        client.admin.command("ping")
        for index, business_date in enumerate(dates):
            config = GeneratorConfig(
                seed=int(business_date.strftime("%Y%m%d")),
                records_per_region=records,
            )
            datasets, ground_truth = generate_dataset(config)
            for database, documents in datasets.items():
                _write_documents(
                    client,
                    database,
                    documents,
                    config.batch_id,
                    cleanup_stale=False,
                )
            for filename, content in generate_provider_statements(
                datasets, config
            ).items():
                (statements_dir / filename).write_text(content, encoding="utf-8")

            missing_schedule_index = missing_targets.get(business_date)
            if missing_schedule_index is not None:
                missing_schedule = scheduler.schedules[missing_schedule_index]
                missing_statement = statements_dir / missing_schedule.statement_name(
                    business_date
                )
                missing_statement.unlink(missing_ok=True)
                _emit(
                    "statement_removed",
                    {
                        "business_date": business_date.isoformat(),
                        "region": missing_schedule.region.value,
                        "tenant_id": missing_schedule.tenant_id,
                        "provider": missing_schedule.provider,
                        "filename": missing_statement.name,
                    },
                )

            (truth_dir / f"{business_date.isoformat()}.json").write_text(
                json.dumps(ground_truth, indent=2, sort_keys=True),
                encoding="utf-8",
            )

            # Noon UTC is after every configured 03:00 local run time while
            # remaining on the same calendar date for all four Asian scopes.
            virtual_now = datetime.combine(business_date, time(12), UTC)
            submitted = scheduler.run_due(virtual_now)
            expected_submissions = len(scheduler.schedules) - (
                1 if missing_schedule_index is not None else 0
            )
            if submitted != expected_submissions:
                raise RuntimeError(
                    f"{business_date}: submitted {submitted}/"
                    f"{expected_submissions} expected reconciliation runs"
                )
            _emit(
                "virtual_day_completed",
                {"business_date": business_date.isoformat(), "day": index + 1},
            )
            if business_date != dates[-1]:
                clock.sleep(seconds_per_day)

    _emit("month_simulation_completed", {"start": start.isoformat(), "days": days})


if __name__ == "__main__":
    main()
