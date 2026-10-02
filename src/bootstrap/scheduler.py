"""Process entry point for filesystem-based daily reconciliation."""

import json
import os
import signal
from datetime import UTC, datetime
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
    ("region", "tenant_id", "provider"),
)


def _emit(event: str, context: dict[str, object]) -> None:
    if event in {"statement_missing", "run_failed", "run_submitted"}:
        statement_present.labels(
            region=str(context["region"]),
            tenant_id=str(context.get("tenant_id") or "none"),
            provider=str(context["provider"]),
        ).set(0 if event == "statement_missing" else 1)
    print(json.dumps({"event": event, **context}, separators=(",", ":")), flush=True)


def main() -> None:
    poll_seconds = float(os.getenv("RECON_SCHEDULER_POLL_SECONDS", "60"))
    if poll_seconds <= 0:
        raise ValueError("RECON_SCHEDULER_POLL_SECONDS must be positive")
    start_http_server(int(os.getenv("RECON_SCHEDULER_METRICS_PORT", "9101")))
    schedules = load_schedules(os.getenv("RECON_SCHEDULES_JSON", "[]"))
    scheduler = DailyReconciliationScheduler(
        Path(os.getenv("RECON_STATEMENT_DIR", "/statements")),
        schedules,
        HttpReconciliationSubmitter(
            os.getenv("RECON_API_URL", "http://api:8000"),
            float(os.getenv("RECON_API_TIMEOUT_SECONDS", "30")),
        ),
        _emit,
    )
    stopped = Event() # boolean flag, default is False

    def stop(_signum, _frame) -> None:
        stopped.set() # stopped = True

    signal.signal(signal.SIGTERM, stop) # SIGTERM: kill from system
    signal.signal(signal.SIGINT, stop) # SIGINT: ctrl+C from user
    _emit("scheduler_started", {"schedule_count": len(schedules)})
    while not stopped.is_set():
        scheduler.run_due(datetime.now(UTC))
        stopped.wait(poll_seconds)
    _emit("scheduler_stopped", {})


if __name__ == "__main__":
    main()
