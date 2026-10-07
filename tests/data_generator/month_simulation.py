"""Generate daily fixtures for a virtual month."""

import os
from datetime import date, timedelta
from pathlib import Path
import json
from pymongo import MongoClient

from .config import scope_specs
from .generator import (
    GeneratorConfig,
    iter_scope_documents,
    scope_document_count,
    write_streaming_ground_truth,
)
from .mongo_seed import _write_document_iterator
from .oracle import write_provider_statement


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


def _emit(event: str, context: dict[str, object]) -> None:
    print(json.dumps({"event": event, **context}, separators=(",", ":")), flush=True)


def generate_data() -> None:
    start = date.fromisoformat(os.getenv("SIMULATION_START_DATE", "2026-09-01"))
    days = int(os.getenv("SIMULATION_DAYS", "30"))
    missing_statement_count = int(
        os.getenv("SIMULATION_MISSING_STATEMENT_COUNT", "2")
    )
    write_ground_truth = (
        os.getenv("SIMULATION_WRITE_GROUND_TRUTH", "false").lower() == "true"
    )
    records = int(os.getenv("RECORDS_PER_REGION", "100"))
    mongo_uri = os.getenv("MONGO_URI", "mongodb://mongodb:27017")
    output = Path(os.getenv("SIMULATION_OUTPUT_DIR", "/test-data/month-simulation"))
    statements_dir = output / "provider-statements"
    truth_dir = output / "ground-truth"
    statements_dir.mkdir(parents=True, exist_ok=True)
    if write_ground_truth:
        truth_dir.mkdir(parents=True, exist_ok=True)
    dates = simulation_dates(start, days)
    missing_targets = missing_statement_targets(dates, missing_statement_count)

    with MongoClient(mongo_uri, serverSelectionTimeoutMS=10_000) as client:
        client.admin.command("ping")
        for business_date in dates:
            config = GeneratorConfig(
                seed=int(business_date.strftime("%Y%m%d")),
                records_per_region=records,
            )
            scopes = scope_specs(config)
            for scope in scopes:
                _write_document_iterator(
                    client,
                    scope.database,
                    iter_scope_documents(config, scope),
                    config.batch_id,
                    cleanup_stale=False,
                )
                write_provider_statement(
                    statements_dir / scope.filename,
                    config,
                    scope,
                    scope_document_count(config, scope),
                )

            missing_scope_index = missing_targets.get(business_date)
            if missing_scope_index is not None:
                missing_scope = scopes[missing_scope_index]
                missing_statement = statements_dir / missing_scope.filename
                missing_statement.unlink(missing_ok=True)
                _emit(
                    "statement_removed",
                    {
                        "business_date": business_date.isoformat(),
                        "region": missing_scope.region.value,
                        "tenant_id": missing_scope.tenant_id,
                        "provider": missing_scope.provider,
                        "filename": missing_statement.name,
                    },
                )

            if write_ground_truth:
                write_streaming_ground_truth(
                    truth_dir / f"{business_date.isoformat()}.json", config
                )
            _emit("simulation_data_generated", {"business_date": business_date.isoformat()})

    _emit("simulation_generation_completed", {"start": start.isoformat(), "days": days})


def main() -> None:
    generate_data()


if __name__ == "__main__":
    main()
