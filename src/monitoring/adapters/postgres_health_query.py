from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from uuid import UUID

from monitoring.application import MonitoringUnavailable
from monitoring.application.health_query import (
    AlertItem,
    AlertPage,
    AlertQuery,
    FreshnessItem,
    FreshnessPage,
    FreshnessQuery,
    ProviderHealthItem,
    ProviderHealthPage,
    ProviderHealthQuery,
)
from monitoring.domain import AlertStatus, FreshnessStatus, HealthStatus, Severity
from shared_kernel import Region


def _dsn(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


class PostgresHealthQueryRepository:
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = _dsn(
            dsn
            or os.getenv(
                "DATABASE_URL",
                "postgresql://user:password@postgres:5432/paymenthub",
            )
        )

    def _connect(self):
        import psycopg

        return psycopg.connect(self.dsn)

    def _rows(self, sql: str, parameters: list[Any]):
        import psycopg

        try:
            with self._connect() as connection:
                connection.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (os.getenv("MONITORING_QUERY_TIMEOUT", "2000ms"),),
                )
                return connection.execute(sql, parameters).fetchall()
        except (
            psycopg.errors.QueryCanceled,
            psycopg.OperationalError,
            psycopg.InterfaceError,
        ) as error:
            raise MonitoringUnavailable(
                "Monitoring database is unavailable"
            ) from error

    def list_provider_health(
        self, query: ProviderHealthQuery
    ) -> ProviderHealthPage:
        where: list[str] = []
        parameters: list[Any] = []
        if query.region is not None:
            where.append("latest.region = %s")
            parameters.append(query.region.value)
        if query.tenant_id is not None:
            where.append("latest.tenant_scope = %s")
            parameters.append(query.tenant_id.strip())
        if query.provider is not None:
            where.append("latest.provider = %s")
            parameters.append(query.provider.strip().upper())
        if query.status is not None:
            where.append("latest.health_status = %s")
            parameters.append(query.status.value)
        predicate = "WHERE " + " AND ".join(where) if where else ""
        parameters.append(query.limit)
        rows = self._rows(
            f"""
            WITH latest AS (
                SELECT DISTINCT ON (region, tenant_scope, provider)
                       region, tenant_scope, provider, health_status,
                       reasons, evaluations, evaluated_at, data_as_of
                FROM health_evaluation_run
                ORDER BY region, tenant_scope, provider, evaluated_at DESC
            )
            SELECT latest.region, latest.tenant_scope, latest.provider,
                   latest.health_status, latest.reasons, latest.evaluations,
                   latest.evaluated_at, latest.data_as_of,
                   COALESCE(
                       array_agg(alert.alert_id::text ORDER BY alert.alert_id)
                           FILTER (WHERE alert.alert_id IS NOT NULL),
                       ARRAY[]::text[]
                   ) AS open_alert_ids
            FROM latest
            LEFT JOIN alert ON alert.region = latest.region
                AND alert.tenant_scope = latest.tenant_scope
                AND alert.provider = latest.provider
                AND alert.status = 'OPEN'
            {predicate}
            GROUP BY latest.region, latest.tenant_scope, latest.provider,
                     latest.health_status, latest.reasons, latest.evaluations,
                     latest.evaluated_at, latest.data_as_of
            ORDER BY latest.region, latest.tenant_scope, latest.provider
            LIMIT %s
            """,
            parameters,
        )
        items = tuple(
            ProviderHealthItem(
                region=Region(row[0]),
                tenant_id=row[1] or None,
                provider=row[2],
                status=HealthStatus(row[3]),
                reasons=tuple(row[4]),
                evaluations=tuple(row[5]),
                evaluated_at=row[6],
                data_as_of=row[7],
                open_alert_ids=tuple(row[8]),
            )
            for row in rows
        )
        return ProviderHealthPage(
            items=items,
            data_as_of=max(
                (item.data_as_of for item in items if item.data_as_of is not None),
                default=None,
            ),
        )

    def list_alerts(self, query: AlertQuery) -> AlertPage:
        where: list[str] = []
        parameters: list[Any] = []
        self._scope_filters(query, where, parameters)
        if query.status is not None:
            where.append("status = %s")
            parameters.append(query.status.value)
        if query.severity is not None:
            where.append("severity = %s")
            parameters.append(query.severity.value)
        if query.rule_id is not None:
            where.append("rule_id = %s")
            parameters.append(query.rule_id.strip())
        if query.cursor_opened_at is not None:
            where.append("(opened_at, alert_id) < (%s, %s)")
            parameters.extend((query.cursor_opened_at, query.cursor_alert_id))
        predicate = "WHERE " + " AND ".join(where) if where else ""
        parameters.append(query.limit + 1)
        rows = self._rows(
            f"""
            SELECT alert_id, region, tenant_scope, provider, rule_id,
                   rule_version, status, severity, health_impact, reason_code,
                   reason_message, observed_value, comparison, threshold,
                   sample_size, minimum_sample, window_size_minutes,
                   evaluated_window_count, reasons, occurrence_count,
                   cooldown_seconds, runbook_ref, data_as_of, opened_at,
                   last_seen_at, last_evaluated_at, last_notified_at, resolved_at
            FROM alert
            {predicate}
            ORDER BY opened_at DESC, alert_id DESC
            LIMIT %s
            """,
            parameters,
        )
        has_more = len(rows) > query.limit
        rows = rows[: query.limit]
        items = tuple(self._alert_item(row) for row in rows)
        next_key = (
            (items[-1].opened_at, UUID(items[-1].alert_id))
            if has_more and items
            else None
        )
        return AlertPage(
            items=items,
            data_as_of=max(
                (item.data_as_of for item in items if item.data_as_of is not None),
                default=None,
            ),
            next_key=next_key,
        )

    def list_freshness(self, query: FreshnessQuery) -> FreshnessPage:
        where: list[str] = []
        parameters: list[Any] = []
        self._scope_filters(query, where, parameters)
        if query.status is not None:
            where.append("freshness_status = %s")
            parameters.append(query.status.value)
        predicate = "WHERE " + " AND ".join(where) if where else ""
        parameters.append(query.limit)
        rows = self._rows(
            f"""
            SELECT region, tenant_scope, provider, last_run_at,
                   last_success_at, data_as_of, freshness_lag_seconds,
                   freshness_sla_seconds, freshness_status,
                   consecutive_failures, last_error_code
            FROM health_runner_status
            {predicate}
            ORDER BY region, tenant_scope, provider
            LIMIT %s
            """,
            parameters,
        )
        items = tuple(
            FreshnessItem(
                source_name="provider-health",
                region=Region(row[0]),
                tenant_id=row[1] or None,
                provider=row[2],
                last_run_at=row[3],
                last_success_at=row[4],
                data_as_of=row[5],
                evaluated_at=row[3],
                lag_seconds=row[6],
                sla_seconds=row[7],
                sla_status=FreshnessStatus(row[8]),
                consecutive_failures=row[9],
                last_error_code=row[10],
            )
            for row in rows
        )
        return FreshnessPage(
            items=items,
            data_as_of=max(
                (item.data_as_of for item in items if item.data_as_of is not None),
                default=None,
            ),
        )

    @staticmethod
    def _scope_filters(query, where: list[str], parameters: list[Any]) -> None:
        if query.region is not None:
            where.append("region = %s")
            parameters.append(query.region.value)
        if query.tenant_id is not None:
            where.append("tenant_scope = %s")
            parameters.append(query.tenant_id.strip())
        if query.provider is not None:
            where.append("provider = %s")
            parameters.append(query.provider.strip().upper())

    @staticmethod
    def _alert_item(row: tuple[Any, ...]) -> AlertItem:
        return AlertItem(
            alert_id=str(row[0]),
            region=Region(row[1]),
            tenant_id=row[2] or None,
            provider=row[3],
            rule_id=row[4],
            rule_version=row[5],
            status=AlertStatus(row[6]),
            severity=Severity(row[7]),
            health_impact=row[8],
            reason_code=row[9],
            reason_message=row[10],
            observed_value=row[11],
            comparison=row[12],
            threshold=row[13],
            sample_size=row[14],
            minimum_sample=row[15],
            window_size_minutes=row[16],
            evaluated_window_count=row[17],
            reasons=tuple(row[18]),
            occurrence_count=row[19],
            cooldown_seconds=row[20],
            runbook_ref=row[21],
            data_as_of=row[22],
            opened_at=row[23],
            last_seen_at=row[24],
            last_evaluated_at=row[25],
            last_notified_at=row[26],
            resolved_at=row[27],
        )
