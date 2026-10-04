from datetime import UTC, date, datetime, time
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from reconciliation.adapters.daily_scheduler import DailySchedule
from reconciliation.adapters.freshness import FreshnessService, router
from shared_kernel import Region


NOW = datetime(2026, 10, 4, 3, 6, tzinfo=UTC)
SCHEDULE = DailySchedule(
    region=Region.VN,
    tenant_id=None,
    provider="ONEPAY",
    business_timezone="UTC",
    cutoff_time=time(2),
    run_time=time(3),
    filename="onepay.csv",
    freshness_sla_seconds=300,
)


class Reader:
    def __init__(self, runs=()):
        self.values = list(runs)

    def runs(self, **filters):
        return list(self.values)


def run(status, business_date=date(2026, 10, 4), number=1):
    return {
        "run_id": UUID(f"00000000-0000-0000-0000-{number:012d}"),
        "business_date": business_date,
        "status": status,
        "started_at": datetime.combine(business_date, time(3), UTC),
        "finished_at": (
            datetime.combine(business_date, time(3, 1), UTC)
            if status in {"COMPLETED", "FAILED"} else None
        ),
    }


def client(runs=()) -> TestClient:
    app = FastAPI()
    app.state.freshness = FreshnessService(
        (SCHEDULE,), Reader(runs), clock=lambda: NOW,
    )
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize(
    ("runs", "expected_status", "reason"),
    [
        ([run("COMPLETED")], "FRESH", "RUN_COMPLETED"),
        ([run("RUNNING")], "LATE", "RUN_NOT_COMPLETED"),
        ([], "MISSING", "NO_RUN_AFTER_SLA"),
        (
            [run("FAILED"), run("FAILED", date(2026, 10, 3), 2)],
            "FAILED",
            "CONSECUTIVE_FAILURES",
        ),
    ],
)
def test_freshness_states(runs, expected_status, reason) -> None:
    response = client(runs).get("/_meta/freshness")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["status"] == expected_status
    assert item["reason"] == reason
    assert item["business_date"] == "2026-10-04"
    assert response.json()["meta"]["data_as_of"] == NOW.isoformat()


def test_filters_can_return_no_schedule() -> None:
    response = client().get("/_meta/freshness", params={"region": "INDIA"})

    assert response.status_code == 200
    assert response.json()["items"] == []
