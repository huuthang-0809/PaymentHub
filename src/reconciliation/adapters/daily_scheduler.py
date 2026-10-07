"""Small daily trigger that submits statements already present on disk."""

import json
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Callable
from urllib.request import Request, urlopen
from uuid import uuid4
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
            )
            if (
                not schedule.provider
                or schedule.cutoff_time.tzinfo is not None
                or schedule.run_time.tzinfo is not None
                or schedule.run_time < schedule.cutoff_time
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
    def __init__(self, api_url: str, timeout_seconds: float = 30) -> None:
        self.endpoint = api_url.rstrip("/") + "/reconciliation-runs"
        self.timeout_seconds = timeout_seconds

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
        with urlopen(request, timeout=self.timeout_seconds) as response:
            response.read()


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