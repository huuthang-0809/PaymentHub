from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import httpx
import pytest

from monitoring.adapters.http import create_app
from monitoring.application import (
    AlertItem,
    AlertPage,
    FreshnessItem,
    FreshnessPage,
    MonitoringUnavailable,
    ProviderHealthItem,
    ProviderHealthPage,
)
from monitoring.domain import (
    AlertStatus,
    FreshnessStatus,
    HealthStatus,
    Severity,
)
from shared_kernel import Region

NOW = datetime(2026, 10, 5, 10, 31, tzinfo=UTC)
ALERT_1 = "00000000-0000-0000-0000-000000000002"
ALERT_2 = "00000000-0000-0000-0000-000000000001"


class BaseRepository:
    def is_ready(self):
        return True

    def list_transactions(self, **kwargs):
        return ()

    def list_window_metrics(self, query):
        raise AssertionError("not used")


class HealthRepository:
    def __init__(self) -> None:
        self.calls = []
        self.error = None
        self.alert_page_number = 0
        self.provider_next_offset = None
        self.freshness_next_offset = None

    def _raise(self):
        if self.error is not None:
            raise self.error

    def list_provider_health(self, query):
        self._raise()
        self.calls.append(query)
        return ProviderHealthPage(
            items=(
                ProviderHealthItem(
                    region=Region.ASIA,
                    tenant_id="asia-id",
                    provider="ESPAY",
                    status=HealthStatus.UNHEALTHY,
                    reasons=({"reason_code": "LOW_RATE", "raw": "secret"},),
                    evaluations=({"status": "BREACH", "sample_size": 120},),
                    evaluated_at=NOW,
                    data_as_of=NOW,
                    open_alert_ids=(ALERT_1,),
                ),
            ),
            data_as_of=NOW,
            next_offset=self.provider_next_offset,
        )

    def list_alerts(self, query):
        self._raise()
        self.calls.append(query)
        self.alert_page_number += 1
        item = AlertItem(
            alert_id=ALERT_1 if self.alert_page_number == 1 else ALERT_2,
            region=Region.ASIA,
            tenant_id="asia-id",
            provider="ESPAY",
            rule_id="DEMO_LOW_SUCCESS_RATE_V1",
            rule_version=1,
            status=AlertStatus.OPEN,
            severity=Severity.CRITICAL,
            health_impact="UNHEALTHY",
            reason_code="THRESHOLD_BREACH",
            reason_message="rate breached configured threshold",
            observed_value=Decimal("0.500000"),
            comparison="LT",
            threshold=Decimal("0.800000"),
            sample_size=120,
            minimum_sample=100,
            window_size_minutes=5,
            evaluated_window_count=1,
            reasons=({"reason_code": "THRESHOLD_BREACH", "raw": "secret"},),
            occurrence_count=1,
            cooldown_seconds=900,
            runbook_ref="fixture://low-success-rate",
            data_as_of=NOW,
            opened_at=NOW,
            last_seen_at=NOW,
            last_evaluated_at=NOW,
            last_notified_at=None,
            resolved_at=None,
            txn_ids=("txn-1", "txn-2", "txn-3", "txn-4", "txn-5"),
        )
        return AlertPage(
            items=(item,),
            data_as_of=NOW,
            next_key=(NOW, UUID(ALERT_1)) if self.alert_page_number == 1 else None,
        )

    def list_freshness(self, query):
        self._raise()
        self.calls.append(query)
        return FreshnessPage(
            items=(
                FreshnessItem(
                    source_name="provider-health",
                    region=Region.INDIA,
                    tenant_id=None,
                    provider="RAZORPAY",
                    last_run_at=NOW,
                    last_success_at=NOW,
                    data_as_of=None,
                    evaluated_at=NOW,
                    lag_seconds=None,
                    sla_seconds=600,
                    sla_status=FreshnessStatus.NO_DATA,
                    consecutive_failures=0,
                    last_error_code=None,
                ),
            ),
            data_as_of=None,
            next_offset=self.freshness_next_offset,
        )


def _get(app, path: str):
    async def request():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path, headers={"X-Trace-Id": "trace-health-api"})

    return asyncio.run(request())


def test_provider_health_returns_committed_snapshot_and_scoped_filters() -> None:
    health = HealthRepository()
    response = _get(
        create_app(BaseRepository(), health),
        "/health/providers?region=ASIA&tenant_id=asia-id&provider=ESPAY&status=UNHEALTHY&limit=5",
    )

    assert response.status_code == 200
    document = response.json()
    assert document["meta"] == {
        "count": 1,
        "data_as_of": "2026-10-05T10:31:00Z",
        "next_cursor": None,
        "trace_id": "trace-health-api",
    }
    assert document["data"][0]["open_alert_ids"] == [ALERT_1]
    assert document["data"][0]["reasons"][0]["raw"] == "***MASKED***"
    query = health.calls[0]
    assert (query.region, query.tenant_id, query.provider, query.status, query.limit) == (
        Region.ASIA,
        "asia-id",
        "ESPAY",
        HealthStatus.UNHEALTHY,
        5,
    )


def test_alert_cursor_round_trip_and_tamper_rejection() -> None:
    health = HealthRepository()
    app = create_app(BaseRepository(), health, cursor_secret="test-cursor-secret")
    first = _get(app, "/alerts?status=OPEN&severity=CRITICAL&limit=1")

    assert first.status_code == 200
    cursor = first.json()["meta"]["next_cursor"]
    assert cursor
    assert first.json()["data"][0]["observed_value"] == "0.500000"
    assert first.json()["data"][0]["reasons"][0]["raw"] == "***MASKED***"
    assert first.json()["data"][0]["txn_ids"] == [
        "txn-1", "txn-2", "txn-3", "txn-4", "txn-5"
    ]

    second = _get(app, f"/alerts?limit=1&cursor={cursor}")
    assert second.status_code == 200
    assert second.json()["meta"]["next_cursor"] is None
    query = health.calls[-1]
    assert query.cursor_opened_at == NOW
    assert query.cursor_alert_id == UUID(ALERT_1)

    tampered = _get(app, f"/alerts?cursor={cursor[:-1]}x")
    assert tampered.status_code == 422
    assert tampered.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "secret" not in tampered.text


def test_freshness_no_data_and_validation_envelopes() -> None:
    health = HealthRepository()
    app = create_app(BaseRepository(), health)
    response = _get(
        app,
        "/_meta/freshness?region=INDIA&provider=RAZORPAY&status=NO_DATA",
    )
    assert response.status_code == 200
    assert response.json()["data"][0]["sla_status"] == "NO_DATA"
    assert response.json()["data"][0]["lag_seconds"] is None
    assert response.json()["meta"]["data_as_of"] is None

    invalid = _get(app, "/alerts?severity=INVALID")
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "path",
    ["/health/providers", "/alerts", "/_meta/freshness"],
)
def test_health_endpoints_map_database_timeout_to_503(path: str) -> None:
    health = HealthRepository()
    health.error = MonitoringUnavailable("postgres timeout with secret details")
    response = _get(create_app(BaseRepository(), health), path)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "secret details" not in response.text


def test_health_and_freshness_cursors_are_signed_and_endpoint_bound() -> None:
    health = HealthRepository()
    health.provider_next_offset = 5
    health.freshness_next_offset = 7
    app = create_app(BaseRepository(), health, cursor_secret="test-cursor-secret")

    health_cursor = _get(app, "/health/providers?limit=5").json()["meta"]["next_cursor"]
    response = _get(app, f"/health/providers?limit=5&cursor={health_cursor}")
    assert response.status_code == 200
    assert health.calls[-1].offset == 5

    wrong_endpoint = _get(app, f"/_meta/freshness?cursor={health_cursor}")
    assert wrong_endpoint.status_code == 422

    freshness_cursor = _get(app, "/_meta/freshness?limit=7").json()["meta"]["next_cursor"]
    response = _get(app, f"/_meta/freshness?limit=7&cursor={freshness_cursor}")
    assert response.status_code == 200
    assert health.calls[-1].offset == 7


@pytest.mark.parametrize(
    "path",
    [
        "/health/providers?sort=provider",
        "/alerts?sort=severity",
        "/_meta/freshness?sort=-last_run_at",
    ],
)
def test_health_api_rejects_non_whitelisted_sort(path: str) -> None:
    response = _get(create_app(BaseRepository(), HealthRepository()), path)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
