import json
from datetime import UTC, date, datetime
import pytest

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