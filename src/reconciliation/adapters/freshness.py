"""Freshness view derived from schedules and reconciliation run history."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder

from reconciliation.application.ports import QueryUnavailable, ReconQuery
from shared_kernel import Region

from .daily_scheduler import DailySchedule


# APIRouter groups the endpoint without creating another FastAPI application.
router = APIRouter(prefix="/_meta", tags=["meta"])


@dataclass(slots=True)
class FreshnessService:
    schedules: tuple[DailySchedule, ...]
    reader: ReconQuery
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def get(
        self,
        *,
        region: Region | None = None,
        tenant_id: str | None = None,
        provider: str | None = None,
    ) -> dict:
        now = self.clock()
        wanted_provider = provider.strip().upper() if provider else None
        schedules = [
            schedule
            for schedule in self.schedules
            if (region is None or schedule.region is region)
            and (tenant_id is None or schedule.tenant_id == tenant_id)
            and (wanted_provider is None or schedule.provider == wanted_provider)
        ]
        items = [self._evaluate(schedule, now) for schedule in schedules]
        return {"items": items, "meta": {"data_as_of": now}}

    def _evaluate(self, schedule: DailySchedule, now: datetime) -> dict:
        # ZoneInfo applies the schedule's IANA timezone and handles UTC offsets.
        timezone = ZoneInfo(schedule.business_timezone)
        local_now = now.astimezone(timezone)
        expected_date = local_now.date()
        expected_at = datetime.combine(
            expected_date, schedule.run_time, timezone
        )
        if local_now < expected_at:
            # Before today's run time, yesterday is the latest expected run.
            expected_date -= timedelta(days=1)
            expected_at -= timedelta(days=1)
        deadline = expected_at + timedelta(
            seconds=schedule.freshness_sla_seconds
        )

        # ReconQuery reads committed run history without exposing database code here.
        runs = self.reader.runs(
            region=schedule.region.value,
            tenant_id=schedule.tenant_id,
            provider=schedule.provider,
        )
        runs.sort(
            key=lambda run: (run["business_date"], run["started_at"]),
            reverse=True,
        )
        expected_runs = [
            run for run in runs if run["business_date"] == expected_date
        ]
        current = expected_runs[0] if expected_runs else None
        last_success = next(
            (run for run in runs if run["status"] == "COMPLETED"), None
        )
        consecutive_failures = 0
        for run in runs:
            if run["status"] != "FAILED":
                break
            consecutive_failures += 1

        if current and current["status"] == "COMPLETED":
            status, reason = "FRESH", "RUN_COMPLETED"
        elif local_now <= deadline:
            status, reason = "FRESH", "WITHIN_SLA"
        elif current and current["status"] in {"PENDING", "RUNNING"}:
            status, reason = "LATE", "RUN_NOT_COMPLETED"
        elif current and consecutive_failures >= 2:
            status, reason = "FAILED", "CONSECUTIVE_FAILURES"
        elif current:
            status, reason = "LATE", "RUN_FAILED_RETRYABLE"
        else:
            status, reason = "MISSING", "NO_RUN_AFTER_SLA"

        return {
            "scope": {
                "region": schedule.region.value,
                "tenant_id": schedule.tenant_id,
                "provider": schedule.provider,
            },
            "business_date": expected_date,
            "expected_run_at": expected_at,
            "sla_deadline": deadline,
            "status": status,
            "reason": reason,
            "last_run": self._run_view(current),
            "last_success": self._run_view(last_success),
            "consecutive_failures": consecutive_failures,
            "lag_seconds": max(0, int((local_now - deadline).total_seconds())),
        }

    @staticmethod
    def _run_view(run: dict | None) -> dict | None:
        if run is None:
            return None
        return {
            "run_id": run["run_id"],
            "business_date": run["business_date"],
            "status": run["status"],
            "finished_at": run["finished_at"],
        }


@router.get("/freshness")
def get_freshness(
    request: Request,
    region: Region | None = None,
    tenant_id: str | None = None,
    provider: str | None = None,
):
    try:
        # jsonable_encoder converts UUID, date and datetime values to JSON values.
        return jsonable_encoder(request.app.state.freshness.get(
            region=region, tenant_id=tenant_id, provider=provider,
        ))
    except QueryUnavailable as error:
        # HTTPException turns a database read failure into a safe API response.
        raise HTTPException(503, "QUERY_UNAVAILABLE") from error
