"""Small daily trigger that submits statements already present on disk."""

import json
import time as clock
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from shared_kernel import Region


@dataclass(frozen=True, slots=True)
class DailySchedule:
    region: Region
    tenant_id: str | None
    provider: str
    business_timezone: str
    cutoff_time: time
    run_time: time
    filename: str
    # Grace time after run_time before freshness becomes unhealthy.
    freshness_sla_seconds: int = 300

    def statement_name(self, business_date: date) -> str:
        name = self.filename.format(
            business_date=business_date.isoformat(),
            business_date_compact=business_date.strftime("%Y%m%d"),
            provider=self.provider.lower(),
            tenant_id=self.tenant_id or "",
        )
        if not name or Path(name).name != name:
            raise ValueError("Scheduled statement filename must be a file name")
        return name


def load_schedules(raw: str) -> tuple[DailySchedule, ...]:
    try:
        values = json.loads(raw)
        if not isinstance(values, list):
            raise ValueError
        schedules = []
        for value in values:
            if not isinstance(value, dict):
                raise ValueError
            region = Region(value["region"])
            tenant_id = value.get("tenant_id") or None
            if (region is Region.ASIA) != (tenant_id is not None):
                raise ValueError
            timezone = str(value["business_timezone"])
            ZoneInfo(timezone)
            schedule = DailySchedule(
                region=region,
                tenant_id=tenant_id,
                provider=str(value["provider"]).strip().upper(),
                business_timezone=timezone,
                cutoff_time=time.fromisoformat(value["cutoff_time"]),
                run_time=time.fromisoformat(value["run_time"]),
                filename=str(value["filename"]),
                freshness_sla_seconds=int(
                    value.get("freshness_sla_seconds", 300)
                ),
            )
            if (
                not schedule.provider
                or schedule.cutoff_time.tzinfo is not None
                or schedule.run_time.tzinfo is not None
                or schedule.run_time < schedule.cutoff_time
                or schedule.freshness_sla_seconds < 0
            ):
                raise ValueError
            schedule.statement_name(date(2000, 1, 2))
            schedules.append(schedule)
        result = tuple(schedules)
        identities = {
            (item.region, item.tenant_id, item.provider) for item in result
        }
        if len(identities) != len(result):
            raise ValueError
        return result
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError("Invalid RECON_SCHEDULES_JSON") from error


SubmitStatement = Callable[[DailySchedule, date, Path], None]
EmitEvent = Callable[[str, dict[str, object]], None]


class DailyReconciliationScheduler:
    def __init__(
        self,
        statement_directory: Path,
        schedules: tuple[DailySchedule, ...],
        submit: SubmitStatement,
        emit: EmitEvent,
    ) -> None:
        self.statement_directory = statement_directory.resolve()
        self.schedules = schedules
        self.submit = submit
        self.emit = emit
        self.completed: set[tuple[Region, str | None, str, date]] = set()

    def run_due(self, now: datetime) -> int:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Scheduler time must be timezone-aware")
        submitted = 0
        for schedule in self.schedules:
            local_now = now.astimezone(ZoneInfo(schedule.business_timezone))
            business_date = local_now.date()
            if local_now.time().replace(tzinfo=None) < schedule.run_time:
                continue
            key = (
                schedule.region,
                schedule.tenant_id,
                schedule.provider,
                business_date,
            )
            if key in self.completed:
                continue
            statement = self.statement_directory / schedule.statement_name(business_date)
            if not statement.is_file():
                self.emit("statement_missing", self._context(schedule, business_date))
                continue
            try:
                self.submit(schedule, business_date, statement)
            except Exception as error:
                self.emit(
                    "run_failed",
                    {
                        **self._context(schedule, business_date),
                        "error": type(error).__name__,
                    },
                )
                continue
            self.completed.add(key)
            submitted += 1
            self.emit("run_submitted", self._context(schedule, business_date))
        return submitted

    @staticmethod
    def _context(schedule: DailySchedule, business_date: date) -> dict[str, object]:
        return {
            "business_date": business_date.isoformat(),
            "region": schedule.region.value,
            "tenant_id": schedule.tenant_id,
            "provider": schedule.provider,
        }


class HttpReconciliationSubmitter:
    def __init__(
        self,
        api_url: str,
        timeout_seconds: float = 30,
        poll_interval_seconds: float = 1,
        poll_timeout_seconds: float = 3600,
    ) -> None:
        self.endpoint = api_url.rstrip("/") + "/reconciliation-runs"
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self.poll_timeout_seconds = poll_timeout_seconds
        if min(
            timeout_seconds, poll_interval_seconds, poll_timeout_seconds
        ) <= 0:
            raise ValueError("HTTP and polling timeouts must be positive")

    def __call__(
        self,
        schedule: DailySchedule,
        business_date: date,
        statement: Path,
    ) -> None:
        fields = {
            "business_date": business_date.isoformat(),
            "region": schedule.region.value,
            "provider": schedule.provider,
            "business_timezone": schedule.business_timezone,
            "cutoff_time": schedule.cutoff_time.isoformat(),
        }
        if schedule.tenant_id is not None:
            fields["tenant_id"] = schedule.tenant_id
        boundary = "paymenthub-" + uuid4().hex
        body = _multipart(fields, statement, boundary)
        request = Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        accepted = self._json_request(request)
        try:
            run_id = str(UUID(str(accepted["run_id"])))
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError("Invalid reconciliation acceptance response") from error
        runs_url = f"{self.endpoint}?{urlencode({'run_id': run_id, 'limit': 1})}"

        deadline = clock.monotonic() + self.poll_timeout_seconds
        while clock.monotonic() < deadline:
            current = self._json_request(Request(runs_url, method="GET"))
            items = current.get("items")
            if not isinstance(items, list):
                raise RuntimeError("Invalid reconciliation runs response")
            if not items:
                clock.sleep(self.poll_interval_seconds)
                continue
            run_status = items[0].get("status")
            if run_status == "COMPLETED":
                return
            if run_status == "FAILED":
                raise RuntimeError(f"Reconciliation run failed: {run_id}")
            if run_status not in {"PENDING", "RUNNING"}:
                raise RuntimeError(
                    f"Unknown reconciliation status: {run_status!r}"
                )
            clock.sleep(self.poll_interval_seconds)
        raise TimeoutError(f"Reconciliation polling timed out: {run_id}")

    def _json_request(self, request: Request) -> dict:
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read())
        if not isinstance(payload, dict):
            raise RuntimeError("Expected a JSON object from reconciliation API")
        return payload


def _multipart(fields: dict[str, str], statement: Path, boundary: str) -> bytes:
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            value.encode(),
            b"\r\n",
        ])
    parts.extend([
        f"--{boundary}\r\n".encode(),
        (
            'Content-Disposition: form-data; name="statement"; '
            f'filename="{statement.name}"\r\n'
        ).encode(),
        b"Content-Type: text/csv\r\n\r\n",
        statement.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    return b"".join(parts)
