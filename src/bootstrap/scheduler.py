"""Process entry point for filesystem-based daily reconciliation."""

import json
import os
import signal
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from threading import Event

from prometheus_client import Gauge, start_http_server

from reconciliation.adapters.daily_scheduler import (
    DailyReconciliationScheduler,
    HttpReconciliationSubmitter,
    load_schedules,
)

statement_present = Gauge(
    "paymenthub_provider_statement_present",
    "Whether the provider statement exists after its scheduled run time.",
    ("region", "tenant_id", "provider", "business_date"),
)


def _emit(event: str, context: dict[str, object]) -> None:
    if event in {"statement_missing", "run_failed", "run_submitted"}:
        statement_present.labels(
            region=str(context["region"]),
            tenant_id=str(context.get("tenant_id") or "none"),
            provider=str(context["provider"]),
            business_date=str(context["business_date"]),
        ).set(0 if event == "statement_missing" else 1)
    print(json.dumps({"event": event, **context}, separators=(",", ":")), flush=True)


def virtual_times(start: date, days: int) -> tuple[datetime, ...]:
    if days <= 0:
        raise ValueError("RECON_SIMULATION_DAYS must be positive")
    return tuple(
        datetime.combine(start + timedelta(days=offset), time(12), UTC)
        for offset in range(days)
    )


def main() -> None:
    poll_seconds = float(os.getenv("RECON_SCHEDULER_POLL_SECONDS", "60"))
    if poll_seconds <= 0:
        raise ValueError("RECON_SCHEDULER_POLL_SECONDS must be positive")
    worker_count = int(os.getenv("RECON_WORKER_COUNT", "1"))
    if worker_count <= 0:
        raise ValueError("RECON_WORKER_COUNT must be positive")
    start_http_server(int(os.getenv("RECON_SCHEDULER_METRICS_PORT", "9101")))
    schedules = load_schedules(os.getenv("RECON_SCHEDULES_JSON", "[]"))
    scheduler = DailyReconciliationScheduler(
        Path(os.getenv("RECON_STATEMENT_DIR", "/statements")),
        schedules,
        HttpReconciliationSubmitter(
            os.getenv("RECON_API_URL", "http://api:8000"),
            float(os.getenv("RECON_API_TIMEOUT_SECONDS", "30")),
            float(os.getenv("RECON_STATUS_POLL_SECONDS", "1")),
            float(os.getenv("RECON_STATUS_TIMEOUT_SECONDS", "3600")),
        ),
        _emit,
        worker_count=worker_count,
    )
    stopped = Event() # boolean flag, default is False

    def stop(_signum, _frame) -> None:
        stopped.set() # stopped = True

    signal.signal(signal.SIGTERM, stop) # SIGTERM: kill from system
    signal.signal(signal.SIGINT, stop) # SIGINT: ctrl+C from user
    _emit(
        "scheduler_started",
        {"schedule_count": len(schedules), "worker_count": worker_count},
    )

    simulation_start = os.getenv("RECON_SIMULATION_START_DATE")
    if simulation_start:
        seconds_per_day = float(
            os.getenv("RECON_SIMULATION_SECONDS_PER_DAY", "2")
        )
        if seconds_per_day < 0:
            raise ValueError(
                "RECON_SIMULATION_SECONDS_PER_DAY must not be negative"
            )
        times = virtual_times(
            date.fromisoformat(simulation_start),
            int(os.getenv("RECON_SIMULATION_DAYS", "1")),
        )
        for index, now in enumerate(times):
            if stopped.is_set():
                break
            scheduler.run_due(now)
            _emit(
                "virtual_day_completed",
                {"business_date": now.date().isoformat(), "day": index + 1},
            )
            if now != times[-1]:
                stopped.wait(seconds_per_day)
        if not stopped.is_set():
            _emit("scheduler_simulation_completed", {"days": len(times)})
            stopped.wait()
        _emit("scheduler_stopped", {})
        return

    while not stopped.is_set():
        scheduler.run_due(datetime.now(UTC))
        stopped.wait(poll_seconds)
    _emit("scheduler_stopped", {})


if __name__ == "__main__":
    main()
