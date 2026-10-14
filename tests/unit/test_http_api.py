import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from canonical.domain import CanonicalTransaction
from monitoring.adapters.http import create_app
from monitoring.application import (
    MonitoringUnavailable,
    WindowMetric,
    WindowMetricPage,
)
from shared_kernel import AmountStatus, CanonicalStatus, Region, TxnType


class FakeRepository:
    def __init__(self, ready=True):
        self.ready = ready
        self.calls = []
        self.window_error = None
        self.window_next_offset = None

    def is_ready(self):
        return self.ready


    def list_window_metrics(self, query):
        self.calls.append(query)
        if self.window_error is not None:
            raise self.window_error
        computed_at = datetime(2026, 9, 25, 11, 0, tzinfo=UTC)
        return WindowMetricPage(
            items=(
                WindowMetric(
                    window_size_minutes=5,
                    window_start=datetime(2026, 9, 25, 10, 0, tzinfo=UTC),
                    window_end=datetime(2026, 9, 25, 10, 5, tzinfo=UTC),
                    region=Region.ASIA,
                    tenant_id="asia-id",
                    provider="ESPAY",
                    txn_type="PAYMENT",
                    instrument="QR",
                    channel_raw="QR",
                    currency="IDR",
                    total_event_count=10,
                    operation_event_count=10,
                    success_count=8,
                    failed_count=1,
                    pending_count=1,
                    unknown_count=0,
                    success_rate=Decimal("0.800000"),
                    failed_rate=Decimal("0.100000"),
                    unknown_rate=Decimal("0.000000"),
                    monetary_count=8,
                    monetary_amount_minor=800000,
                    invalid_amount_count=0,
                    missing_currency_count=0,
                    parse_error_count=0,
                    late_event_count=2,
                    non_terminal_count=1,
                    pending_age_lt_5m_count=0,
                    pending_age_5_to_15m_count=0,
                    pending_age_15_to_60m_count=1,
                    pending_age_gte_60m_count=0,
                    oldest_pending_age_seconds=3500,
                    freshness_lag_seconds=3360,
                    source_event_count=10,
                    last_event_at=datetime(2026, 9, 25, 10, 4, tzinfo=UTC),
                    computed_at=computed_at,
                ),
            ),
            data_as_of=computed_at,
            next_offset=self.window_next_offset,
        )

    def list_transactions(self, *, region, status, limit):
        self.calls.append((region, status, limit))
        return (
            CanonicalTransaction(
                region=Region.VN,
                tenant_id=None,
                txn_id="txn-1",
                txn_type=TxnType.PAYMENT,
                provider="onepay",
                status=CanonicalStatus.SUCCESS,
                amount_status=AmountStatus.ASSUMED_CURRENCY,
                created_at=datetime(2026, 9, 24, tzinfo=UTC),
                is_terminal=True,
                is_money_movement=True,
                is_reconcilable=True,
                raw={"card": "must-not-leak"},
            ),
        )


def request(app, path, *, headers=None):
    async def send():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path, headers=headers)

    return asyncio.run(send())


def test_live_and_ready_health_are_separate():
    repository = FakeRepository(ready=False)
    app = create_app(repository)
    assert request(app, "/health/live").status_code == 200
    response = request(app, "/health/ready", headers={"X-Trace-Id": "trace-1"})
    assert response.status_code == 503
    assert response.json()["error"]["trace_id"] == "trace-1"


def test_txns_validates_filters_and_masks_raw():
    repository = FakeRepository()
    response = request(
        create_app(repository), "/txns?region=VN&status=SUCCESS&limit=10"
    )
    assert response.status_code == 200
    assert repository.calls == [(Region.VN, CanonicalStatus.SUCCESS, 10)]
    assert response.json()["items"][0]["raw"] == "***MASKED***"
    assert "must-not-leak" not in response.text


def test_invalid_query_uses_safe_error_envelope():
    response = request(create_app(FakeRepository()), "/txns?region=WRONG&limit=0")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "trace_id" in response.json()["error"]


def test_window_metrics_returns_scoped_committed_data():
    repository = FakeRepository()
    response = request(
        create_app(repository),
        "/metrics/windows?region=ASIA&tenant_id=asia-id&window_size=5&limit=10",
        headers={"X-Trace-Id": "window-trace"},
    )

    assert response.status_code == 200
    document = response.json()
    assert document["meta"] == {
        "count": 1,
        "data_as_of": "2026-09-25T11:00:00Z",
        "next_cursor": None,
        "trace_id": "window-trace",
    }
    assert document["data"][0]["tenant_id"] == "asia-id"
    assert document["data"][0]["success_rate"] == "0.800000"
    assert document["data"][0]["monetary_amount_minor"] == "800000"
    assert document["data"][0]["late_event_count"] == 2
    assert document["data"][0]["non_terminal_count"] == 1
    assert document["data"][0]["pending_age_15_to_60m_count"] == 1
    assert document["data"][0]["oldest_pending_age_seconds"] == 3500
    assert document["data"][0]["freshness_lag_seconds"] == 3360
    query = repository.calls[-1]
    assert query.region is Region.ASIA
    assert query.tenant_id == "asia-id"
    assert query.window_size == 5
    assert query.limit == 10


def test_window_metrics_rejects_invalid_window_and_time_range():
    app = create_app(FakeRepository())
    invalid_size = request(app, "/metrics/windows?window_size=7")
    invalid_range = request(
        app,
        "/metrics/windows?start=2026-09-26T00:00:00Z&end=2026-09-25T00:00:00Z",
    )

    assert invalid_size.status_code == 422
    assert invalid_size.json()["error"]["code"] == "VALIDATION_ERROR"
    assert invalid_range.status_code == 422
    assert "later than start" in invalid_range.json()["error"]["message"]


def test_window_metrics_database_timeout_returns_safe_503():
    repository = FakeRepository()
    repository.window_error = MonitoringUnavailable("secret database detail")
    response = request(
        create_app(repository),
        "/metrics/windows",
        headers={"X-Trace-Id": "timeout-trace"},
    )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "SERVICE_UNAVAILABLE",
            "message": "Monitoring data is temporarily unavailable",
            "trace_id": "timeout-trace",
        }
    }
    assert "secret database detail" not in response.text


def test_window_metrics_cursor_round_trip_and_sort_whitelist():
    repository = FakeRepository()
    repository.window_next_offset = 10
    app = create_app(repository, cursor_secret="test-cursor-secret")

    first = request(app, "/metrics/windows?limit=10&sort=-window_start")
    cursor = first.json()["meta"]["next_cursor"]
    assert cursor
    second = request(app, f"/metrics/windows?limit=10&cursor={cursor}")
    assert second.status_code == 200
    assert repository.calls[-1].offset == 10

    invalid = request(app, "/metrics/windows?sort=provider")
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"
