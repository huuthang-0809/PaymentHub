import json
import threading
from datetime import UTC, date, datetime
from uuid import UUID
import pytest

from reconciliation.adapters import daily_scheduler
from reconciliation.adapters.daily_scheduler import (
    DailyReconciliationScheduler,
    HttpReconciliationSubmitter,
    load_schedules,
)

SCHEDULES = json.dumps([{
    "region": "VN",
    "provider": "ONEPAY",
    "business_timezone": "Asia/Ho_Chi_Minh",
    "cutoff_time": "02:00",
    "run_time": "03:00",
    "filename": "onepay_{business_date_compact}.csv",
}])

def test_due_schedule_submits_existing_file_once(tmp_path) -> None:
    statement = tmp_path / "onepay_20261002.csv"
    statement.write_text("provider,region\n", encoding="utf-8")
    submitted = []
    events = []
    scheduler = DailyReconciliationScheduler(
        tmp_path,
        load_schedules(SCHEDULES),
        lambda schedule, business_date, path: submitted.append(
            (schedule.provider, business_date, path)
        ),
        lambda event, context: events.append((event, context)),
    )
    now = datetime(2026, 10, 1, 20, tzinfo=UTC)  # 03:00 on 02/10 in Vietnam

    assert scheduler.run_due(now) == 1
    assert scheduler.run_due(now) == 0
    assert submitted == [("ONEPAY", date(2026, 10, 2), statement)]
    assert events[-1][0] == "run_submitted"

def test_missing_file_is_retried_on_next_poll(tmp_path) -> None:
    submitted = []
    events = []
    scheduler = DailyReconciliationScheduler(
        tmp_path,
        load_schedules(SCHEDULES),
        lambda schedule, business_date, path: submitted.append(path),
        lambda event, context: events.append(event),
    )
    now = datetime(2026, 10, 1, 20, tzinfo=UTC)

    assert scheduler.run_due(now) == 0
    (tmp_path / "onepay_20261002.csv").write_text("csv", encoding="utf-8")
    assert scheduler.run_due(now) == 1
    assert len(submitted) == 1
    assert events == ["statement_missing", "run_submitted"]

def test_schedule_does_not_run_before_local_run_time(tmp_path) -> None:
    called = []
    scheduler = DailyReconciliationScheduler(
        tmp_path, load_schedules(SCHEDULES),
        lambda *args: called.append(args), lambda *args: None,
    )
    assert scheduler.run_due(datetime(2026, 10, 1, 19, 59, tzinfo=UTC)) == 0
    assert called == []


def test_due_schedules_run_up_to_worker_count_in_parallel(tmp_path) -> None:
    schedules = load_schedules(json.dumps([
        {
            "region": "VN",
            "provider": "ONEPAY",
            "business_timezone": "Asia/Ho_Chi_Minh",
            "cutoff_time": "02:00",
            "run_time": "03:00",
            "filename": "onepay_{business_date_compact}.csv",
        },
        {
            "region": "INDIA",
            "provider": "RAZORPAY",
            "business_timezone": "Asia/Kolkata",
            "cutoff_time": "02:00",
            "run_time": "03:00",
            "filename": "razorpay_{business_date_compact}.csv",
        },
    ]))
    (tmp_path / "onepay_20261002.csv").write_text("csv", encoding="utf-8")
    (tmp_path / "razorpay_20261002.csv").write_text("csv", encoding="utf-8")
    both_workers = threading.Barrier(2)
    worker_threads = set()
    lock = threading.Lock()

    def submit(*_args) -> None:
        with lock:
            worker_threads.add(threading.get_ident())
        both_workers.wait(timeout=2)

    scheduler = DailyReconciliationScheduler(
        tmp_path,
        schedules,
        submit,
        lambda *_args: None,
        worker_count=2,
    )

    assert scheduler.run_due(datetime(2026, 10, 2, 12, tzinfo=UTC)) == 2
    assert len(worker_threads) == 2


def test_scheduler_rejects_non_positive_worker_count(tmp_path) -> None:
    with pytest.raises(ValueError, match="worker_count must be positive"):
        DailyReconciliationScheduler(
            tmp_path,
            load_schedules(SCHEDULES),
            lambda *_args: None,
            lambda *_args: None,
            worker_count=0,
        )

@pytest.mark.parametrize("raw", [
    "{}",
    "[{}]",
    '[{"region":"ASIA"}]',
    json.dumps([{
        "region": "VN", "provider": "ONEPAY",
        "business_timezone": "UTC", "cutoff_time": "03:00",
        "run_time": "02:00", "filename": "onepay.csv",
    }]),
])
def test_invalid_schedule_configuration_is_rejected(raw) -> None:
    with pytest.raises(ValueError, match="Invalid RECON_SCHEDULES_JSON"):
        load_schedules(raw)


class JsonResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self) -> bytes:
        return self.payload


def test_http_submitter_accepts_then_polls_until_completed(
    tmp_path, monkeypatch
) -> None:
    run_id = UUID("00000000-0000-0000-0000-000000000001")
    responses = iter([
        JsonResponse({
            "run_id": str(run_id),
            "status": "PENDING",
        }),
        JsonResponse({"items": [{"run_id": str(run_id), "status": "PENDING"}]}),
        JsonResponse({"items": [{"run_id": str(run_id), "status": "RUNNING"}]}),
        JsonResponse({"items": [{"run_id": str(run_id), "status": "COMPLETED"}]}),
    ])
    requests = []

    def open_request(request, *, timeout):
        requests.append((request, timeout))
        return next(responses)

    monkeypatch.setattr(daily_scheduler, "urlopen", open_request)
    monkeypatch.setattr(daily_scheduler.clock, "sleep", lambda _: None)
    statement = tmp_path / "onepay.csv"
    statement.write_text("provider,region\n", encoding="utf-8")
    schedule = load_schedules(SCHEDULES)[0]

    HttpReconciliationSubmitter(
        "http://api:8000", poll_interval_seconds=0.01
    )(schedule, date(2026, 10, 2), statement)

    assert requests[0][0].get_method() == "POST"
    assert all(request.get_method() == "GET" for request, _ in requests[1:])
    assert all(
        f"run_id={run_id}" in request.full_url for request, _ in requests[1:]
    )
    assert all(timeout == 30 for _, timeout in requests)
