"""Process entry point for filesystem-based daily reconciliation."""

import json
import os
import signal
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from reconciliation.adapters.daily_scheduler import (
    DailyReconciliationScheduler,
    HttpReconciliationSubmitter,
    load_schedules,
)


def _emit(event: str, context: dict[str, object]) -> None:
    print(json.dumps({"event": event, **context}, separators=(",", ":")), flush=True)


def main() -> None:
    poll_seconds = float(os.getenv("RECON_SCHEDULER_POLL_SECONDS", "60"))
    if poll_seconds <= 0:
        raise ValueError("RECON_SCHEDULER_POLL_SECONDS must be positive")
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
    stopped = Event()

    def stop(_signum, _frame) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    _emit("scheduler_started", {"schedule_count": len(schedules)})
    while not stopped.is_set():
        scheduler.run_due(datetime.now(UTC))
        stopped.wait(poll_seconds)
    _emit("scheduler_stopped", {})


if __name__ == "__main__":
    main()
