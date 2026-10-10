import logging
import os
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from monitoring.adapters.logging import log_event, mask_raw
from monitoring.application import (
    AlertCursorCodec,
    AlertItem,
    AlertQuery,
    FreshnessItem,
    FreshnessQuery,
    MonitoringQueryRepository,
    MonitoringQueryService,
    MonitoringUnavailable,
    ProviderHealthItem,
    ProviderHealthQuery,
    WindowMetric,
    WindowMetricQuery,
)
from monitoring.domain import AlertStatus, FreshnessStatus, HealthStatus, Severity
from shared_kernel import CanonicalStatus, Region

LOGGER = logging.getLogger("paymenthub")


def _error(code: str, message: str, trace_id: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "trace_id": trace_id}},
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _decimal(value: object | None) -> str | None:
    return str(value) if value is not None else None


def _window_item(row: WindowMetric) -> dict[str, Any]:
    return {
        "window_size_minutes": row.window_size_minutes,
        "window_start": _iso(row.window_start),
        "window_end": _iso(row.window_end),
        "region": row.region.value,
        "tenant_id": row.tenant_id,
        "provider": row.provider,
        "txn_type": row.txn_type,
        "instrument": row.instrument,
        "channel_raw": row.channel_raw,
        "currency": row.currency,
        "total_event_count": row.total_event_count,
        "operation_event_count": row.operation_event_count,
        "success_count": row.success_count,
        "failed_count": row.failed_count,
        "pending_count": row.pending_count,
        "unknown_count": row.unknown_count,
        "success_rate": _decimal(row.success_rate),
        "failed_rate": _decimal(row.failed_rate),
        "unknown_rate": _decimal(row.unknown_rate),
        "monetary_count": row.monetary_count,
        "monetary_amount_minor": str(row.monetary_amount_minor),
        "invalid_amount_count": row.invalid_amount_count,
        "missing_currency_count": row.missing_currency_count,
        "parse_error_count": row.parse_error_count,
        "late_event_count": row.late_event_count,
        "non_terminal_count": row.non_terminal_count,
        "pending_age_lt_5m_count": row.pending_age_lt_5m_count,
        "pending_age_5_to_15m_count": row.pending_age_5_to_15m_count,
        "pending_age_15_to_60m_count": row.pending_age_15_to_60m_count,
        "pending_age_gte_60m_count": row.pending_age_gte_60m_count,
        "oldest_pending_age_seconds": row.oldest_pending_age_seconds,
        "freshness_lag_seconds": row.freshness_lag_seconds,
        "source_event_count": row.source_event_count,
        "last_event_at": _iso(row.last_event_at),
        "computed_at": _iso(row.computed_at),
    }


def _health_item(row: ProviderHealthItem) -> dict[str, Any]:
    return {
        "region": row.region.value,
        "tenant_id": row.tenant_id,
        "provider": row.provider,
        "status": row.status.value,
        "reasons": mask_raw(list(row.reasons)),
        "evaluations": mask_raw(list(row.evaluations)),
        "evaluated_at": _iso(row.evaluated_at),
        "data_as_of": _iso(row.data_as_of),
        "open_alert_ids": list(row.open_alert_ids),
    }


def _alert_item(row: AlertItem) -> dict[str, Any]:
    return {
        "alert_id": row.alert_id,
        "region": row.region.value,
        "tenant_id": row.tenant_id,
        "provider": row.provider,
        "rule_id": row.rule_id,
        "rule_version": row.rule_version,
        "status": row.status.value,
        "severity": row.severity.value,
        "health_impact": row.health_impact,
        "reason_code": row.reason_code,
        "reason_message": row.reason_message,
        "observed_value": _decimal(row.observed_value),
        "comparison": row.comparison,
        "threshold": _decimal(row.threshold),
        "sample_size": row.sample_size,
        "minimum_sample": row.minimum_sample,
        "window_size_minutes": row.window_size_minutes,
        "evaluated_window_count": row.evaluated_window_count,
        "reasons": mask_raw(list(row.reasons)),
        "occurrence_count": row.occurrence_count,
        "cooldown_seconds": row.cooldown_seconds,
        "runbook_ref": row.runbook_ref,
        "data_as_of": _iso(row.data_as_of),
        "opened_at": _iso(row.opened_at),
        "last_seen_at": _iso(row.last_seen_at),
        "last_evaluated_at": _iso(row.last_evaluated_at),
        "last_notified_at": _iso(row.last_notified_at),
        "resolved_at": _iso(row.resolved_at),
    }


def _freshness_item(row: FreshnessItem) -> dict[str, Any]:
    return {
        "source_name": row.source_name,
        "region": row.region.value,
        "tenant_id": row.tenant_id,
        "provider": row.provider,
        "last_run_at": _iso(row.last_run_at),
        "last_success_at": _iso(row.last_success_at),
        "data_as_of": _iso(row.data_as_of),
        "evaluated_at": _iso(row.evaluated_at),
        "lag_seconds": row.lag_seconds,
        "sla_seconds": row.sla_seconds,
        "sla_status": row.sla_status.value,
        "consecutive_failures": row.consecutive_failures,
        "last_error_code": row.last_error_code,
    }


def create_app(
    repository: MonitoringQueryRepository,
    health_repository: Any | None = None,
    *,
    cursor_secret: str | None = None,
) -> FastAPI:
    app = FastAPI(title="PaymentHub API")
    health_repository = health_repository or repository
    cursor_codec = AlertCursorCodec(
        cursor_secret
        or os.getenv("MONITORING_CURSOR_SECRET", "local-demo-cursor-secret")
    )

    @app.middleware("http")
    async def trace_request(request: Request, call_next: Any):
        request.state.trace_id = request.headers.get("X-Trace-Id") or str(uuid4())
        response = await call_next(request)
        response.headers["X-Trace-Id"] = request.state.trace_id
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        log_event(LOGGER, "request.validation_failed", trace_id=request.state.trace_id)
        return _error("VALIDATION_ERROR", "Request parameters are invalid", request.state.trace_id, 422)

    @app.exception_handler(ValueError)
    async def value_error(request: Request, exc: ValueError):
        log_event(LOGGER, "request.validation_failed", trace_id=request.state.trace_id)
        return _error("VALIDATION_ERROR", str(exc), request.state.trace_id, 422)

    @app.exception_handler(MonitoringUnavailable)
    async def monitoring_unavailable(request: Request, exc: MonitoringUnavailable):
        log_event(LOGGER, "monitoring.query_unavailable", trace_id=request.state.trace_id)
        return _error("SERVICE_UNAVAILABLE", "Monitoring data is temporarily unavailable", request.state.trace_id, 503)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        LOGGER.exception("unhandled request error", extra={"trace_id": request.state.trace_id})
        return _error("INTERNAL_ERROR", "An unexpected error occurred", request.state.trace_id, 500)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    async def ready(request: Request):
        if not repository.is_ready():
            return _error("NOT_READY", "Database is unavailable", request.state.trace_id, 503)
        return {"status": "ready"}

    @app.get("/txns")
    async def transactions(
        request: Request,
        region: Region | None = None,
        status: CanonicalStatus | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        rows = repository.list_transactions(region=region, status=status, limit=limit)
        result = []
        for row in rows:
            item = mask_raw(asdict(row))
            item["region"] = row.region.value
            item["status"] = row.status.value
            item["txn_type"] = row.txn_type.value
            item["amount_status"] = row.amount_status.value
            result.append(item)
            log_event(LOGGER, "transaction.returned", run_id=request.state.trace_id, region=row.region.value, tenant_id=row.tenant_id, txn_id=row.txn_id, raw=row.raw)
        return {"items": result, "count": len(result), "trace_id": request.state.trace_id}

    @app.get("/metrics/windows")
    async def window_metrics(
        request: Request,
        region: Region | None = None,
        tenant_id: str | None = Query(default=None, max_length=100),
        start: datetime | None = None,
        end: datetime | None = None,
        window_size: int = Query(default=5),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        page = MonitoringQueryService(repository).get_windows(
            WindowMetricQuery(region=region, tenant_id=tenant_id, start=start, end=end, window_size=window_size, limit=limit)
        )
        return {"data": [_window_item(row) for row in page.items], "meta": {"count": len(page.items), "data_as_of": _iso(page.data_as_of), "next_cursor": None, "trace_id": request.state.trace_id}}

    @app.get("/health/providers")
    async def provider_health(
        request: Request,
        region: Region | None = None,
        tenant_id: str | None = Query(default=None, max_length=100),
        provider: str | None = Query(default=None, max_length=100),
        status: HealthStatus | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        page = health_repository.list_provider_health(
            ProviderHealthQuery(region, tenant_id, provider, status, limit)
        )
        return {"data": [_health_item(row) for row in page.items], "meta": {"count": len(page.items), "data_as_of": _iso(page.data_as_of), "next_cursor": None, "trace_id": request.state.trace_id}}

    @app.get("/alerts")
    async def alerts(
        request: Request,
        status: AlertStatus | None = None,
        severity: Severity | None = None,
        rule_id: str | None = Query(default=None, max_length=200),
        region: Region | None = None,
        tenant_id: str | None = Query(default=None, max_length=100),
        provider: str | None = Query(default=None, max_length=100),
        limit: int = Query(default=100, ge=1, le=200),
        cursor: str | None = Query(default=None, max_length=1000),
    ) -> dict[str, Any]:
        cursor_key = cursor_codec.decode(cursor) if cursor else (None, None)
        page = health_repository.list_alerts(
            AlertQuery(status, severity, rule_id, region, tenant_id, provider, limit, *cursor_key)
        )
        return {"data": [_alert_item(row) for row in page.items], "meta": {"count": len(page.items), "data_as_of": _iso(page.data_as_of), "next_cursor": cursor_codec.encode(page.next_key) if page.next_key else None, "trace_id": request.state.trace_id}}

    @app.get("/_meta/freshness")
    async def freshness(
        request: Request,
        region: Region | None = None,
        tenant_id: str | None = Query(default=None, max_length=100),
        provider: str | None = Query(default=None, max_length=100),
        status: FreshnessStatus | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        page = health_repository.list_freshness(
            FreshnessQuery(region, tenant_id, provider, status, limit)
        )
        return {"data": [_freshness_item(row) for row in page.items], "meta": {"count": len(page.items), "data_as_of": _iso(page.data_as_of), "next_cursor": None, "trace_id": request.state.trace_id}}

    return app


def install_monitoring_api(
    app: FastAPI,
    repository: MonitoringQueryRepository,
    health_repository: Any | None = None,
) -> None:
    monitoring_app = create_app(repository, health_repository)
    monitoring_paths = {
        "/health/live", "/health/ready", "/txns", "/metrics/windows",
        "/health/providers", "/alerts", "/_meta/freshness",
    }
    for route in monitoring_app.router.routes:
        if getattr(route, "path", None) in monitoring_paths:
            app.router.routes.append(route)
    app.user_middleware.extend(monitoring_app.user_middleware)
    app.exception_handlers.update(monitoring_app.exception_handlers)
