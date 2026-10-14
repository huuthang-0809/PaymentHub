from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from monitoring.domain import (
    AlertRecord,
    AlertStatus,
    AlertTransition,
    Comparison,
    EvaluationScope,
    EvaluationStatus,
    FreshnessStatus,
    HealthEvaluation,
    HealthRunnerStatus,
    HealthImpact,
    HealthMetric,
    HealthReason,
    HealthWindow,
    RuleState,
    Severity,
    classify_source_freshness,
)
from shared_kernel import Region


def _dsn(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _default_dsn(value: str | None) -> str:
    return _dsn(
        value
        or os.getenv(
            "DATABASE_URL",
            "postgresql://user:password@postgres:5432/paymenthub",
        )
    )


def _reason_payload(reason: HealthReason) -> dict[str, Any]:
    return {
        "rule_id": reason.rule_id,
        "rule_version": reason.rule_version,
        "evaluation_status": reason.evaluation_status.value,
        "health_impact": reason.health_impact.value,
        "reason_code": reason.reason_code,
        "message": reason.message,
        "observed_value": (
            str(reason.observed_value)
            if reason.observed_value is not None
            else None
        ),
        "comparison": reason.comparison.value,
        "threshold": str(reason.threshold),
        "sample_size": reason.sample_size,
        "minimum_sample": reason.minimum_sample,
        "window_size_minutes": reason.window_size_minutes,
        "evaluated_window_count": reason.evaluated_window_count,
        "first_window_start": (
            reason.first_window_start.isoformat()
            if reason.first_window_start is not None
            else None
        ),
        "last_window_end": (
            reason.last_window_end.isoformat()
            if reason.last_window_end is not None
            else None
        ),
        "runbook_ref": reason.runbook_ref,
        "data_as_of": (
            reason.data_as_of.isoformat()
            if reason.data_as_of is not None
            else None
        ),
        "txn_ids": list(reason.txn_ids),
    }


class PostgresHealthMetricSource:
    def __init__(self, dsn: str | None = None, *, limit: int = 180) -> None:
        if limit < 1 or limit > 1440:
            raise ValueError("health metric row limit must be between 1 and 1440")
        self.dsn = _default_dsn(dsn)
        self.limit = limit

    def _connect(self) -> Any:
        import psycopg

        return psycopg.connect(self.dsn)

    def list_closed_windows(
        self, scope: EvaluationScope, *, evaluated_at: datetime
    ) -> tuple[HealthWindow, ...]:
        with self._connect() as connection:
            connection.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (os.getenv("HEALTH_QUERY_TIMEOUT", "3000ms"),),
            )
            rows = connection.execute(
                """
                SELECT window_size_minutes, window_start, window_end,
                       sum(operation_event_count)::bigint,
                       sum(success_count)::bigint,
                       sum(failed_count)::bigint,
                       max(last_event_at) AS data_as_of,
                       sum(total_count)::bigint,
                       sum(unknown_count)::bigint,
                       sum(invalid_amount_count)::bigint
                FROM window_aggregate
                WHERE region = %s
                  AND tenant_scope = %s
                  AND provider = %s
                  AND window_end <= %s
                GROUP BY window_size_minutes, window_start, window_end
                ORDER BY window_end DESC, window_size_minutes
                LIMIT %s
                """,
                (
                    scope.region.value,
                    scope.tenant_scope,
                    scope.provider,
                    evaluated_at,
                    self.limit,
                ),
            ).fetchall()
            enriched_rows = []
            for row in rows:
                baseline = connection.execute(
                    """
                    SELECT coalesce(round(avg(slot_total)), 0)::bigint
                    FROM (
                        SELECT window_start, sum(total_count)::bigint AS slot_total
                        FROM window_aggregate
                        WHERE region = %s AND tenant_scope = %s AND provider = %s
                          AND window_size_minutes = %s AND window_start < %s
                          AND window_start >= %s - interval '28 days'
                          AND extract(isodow FROM window_start) = extract(isodow FROM %s::timestamptz)
                          AND extract(hour FROM window_start) = extract(hour FROM %s::timestamptz)
                          AND extract(minute FROM window_start) = extract(minute FROM %s::timestamptz)
                        GROUP BY window_start
                    ) history
                    """,
                    (scope.region.value, scope.tenant_scope, scope.provider,
                     row[0], row[1], row[1], row[1], row[1], row[1]),
                ).fetchone()[0]
                enriched_rows.append((*row, baseline))
            rows = enriched_rows
            queue_row = connection.execute(
                """
                SELECT coalesce(extract(epoch FROM (
                    now() - (min(created_at) FILTER (
                        WHERE status IN ('READY', 'PROCESSING')
                    ))
                )), 0)::bigint,
                    count(*) FILTER (WHERE status = 'DEAD_LETTER')::bigint
                FROM monitoring_queue_message
                """
            ).fetchone()
            pending_row = connection.execute(
                """
                WITH latest AS (
                    SELECT DISTINCT ON (region, tenant_id, txn_id)
                           region, txn_type, status, refund_of, created_at,
                           raw->'__monitoring__'->>'instrument' AS instrument
                    FROM canonical_event
                    WHERE region = %s AND coalesce(tenant_id, '') = %s
                      AND provider = %s AND is_deleted = false AND is_test_data = false
                    ORDER BY region, tenant_id, txn_id, event_time DESC, event_id DESC
                )
                SELECT
                    coalesce(max(extract(epoch FROM (%s - created_at))) FILTER (
                        WHERE region = 'ASIA' AND instrument IN ('QR', 'VIRTUAL_ACCOUNT')
                          AND status IN ('CREATED', 'PENDING')), 0)::bigint,
                    coalesce(max(extract(epoch FROM (%s - created_at))) FILTER (
                        WHERE region = 'ASIA' AND txn_type = 'REFUND' AND refund_of IS NOT NULL
                          AND status IN ('CREATED', 'PENDING')), 0)::bigint,
                    coalesce(max(extract(epoch FROM (%s - created_at))) FILTER (
                        WHERE region = 'INDIA' AND status IN ('CREATED', 'PENDING', 'UNKNOWN')),
                        0)::bigint,
                    coalesce(max(extract(epoch FROM (%s - created_at))) FILTER (
                        WHERE region = 'INDIA' AND txn_type = 'REFUND' AND status = 'CREATED'),
                        0)::bigint
                FROM latest
                """,
                (scope.region.value, scope.tenant_scope, scope.provider,
                 evaluated_at, evaluated_at, evaluated_at, evaluated_at),
            ).fetchone()
            evidence_rows = connection.execute(
                """
                SELECT txn_id, region, txn_type, status, refund_of, created_at,
                       raw->'__monitoring__'->>'instrument' AS instrument,
                       amount_status
                FROM (
                    SELECT DISTINCT ON (region, tenant_id, txn_id)
                           txn_id, region, tenant_id, txn_type, status, refund_of,
                           created_at, event_time, event_id, raw, amount_status
                    FROM canonical_event
                    WHERE region = %s AND coalesce(tenant_id, '') = %s
                      AND provider = %s AND is_deleted = false AND is_test_data = false
                    ORDER BY region, tenant_id, txn_id, event_time DESC, event_id DESC
                ) latest
                ORDER BY created_at ASC, event_id ASC
                LIMIT 200
                """,
                (scope.region.value, scope.tenant_scope, scope.provider),
            ).fetchall()
            evidence: dict[HealthMetric, tuple[str, ...]] = {}

            def ids(predicate) -> tuple[str, ...]:
                return tuple(dict.fromkeys(row[0] for row in evidence_rows if predicate(row)))[:5]

            evidence[HealthMetric.FAILURE_RATE] = ids(
                lambda row: row[3] in {"FAILED", "EXPIRED", "CANCELLED"}
            )
            evidence[HealthMetric.DATA_QUALITY_ERROR_COUNT] = ids(
                lambda row: row[3] == "UNKNOWN" or row[7] == "INVALID"
            )
            evidence[HealthMetric.ASIA_QR_VA_PENDING_OVER_24H] = ids(
                lambda row: row[1] == "ASIA"
                and row[6] in {"QR", "VIRTUAL_ACCOUNT"}
                and row[3] in {"CREATED", "PENDING"}
            )
            evidence[HealthMetric.ASIA_REFUND_SHAPE_B_PENDING] = ids(
                lambda row: row[1] == "ASIA" and row[2] == "REFUND"
                and row[4] is not None and row[3] in {"CREATED", "PENDING"}
            )
            evidence[HealthMetric.INDIA_NON_TERMINAL_OVER_AGE] = ids(
                lambda row: row[1] == "INDIA"
                and row[3] in {"CREATED", "PENDING", "UNKNOWN"}
            )
            evidence[HealthMetric.INDIA_REFUND_CREATED_OVER_AGE] = ids(
                lambda row: row[1] == "INDIA" and row[2] == "REFUND"
                and row[3] == "CREATED"
            )
            heartbeat_row = connection.execute(
                """
                SELECT max(last_heartbeat_at)
                FROM source_activity
                WHERE region = %s AND tenant_scope = %s AND provider = %s
                """,
                (scope.region.value, scope.tenant_scope, scope.provider),
            ).fetchone()
            last_heartbeat_at = heartbeat_row[0] if heartbeat_row else None
        return tuple(
            HealthWindow(
                scope=scope,
                window_size_minutes=row[0],
                window_start=row[1],
                window_end=row[2],
                operation_event_count=row[3],
                success_count=row[4],
                failed_count=row[5],
                # A live heartbeat proves the source/transport is healthy even
                # when there were legitimately no business events. Aggregate
                # APIs still expose event-only freshness separately.
                data_as_of=max(
                    row[6], min(last_heartbeat_at, row[2] - timedelta(microseconds=1))
                )
                if last_heartbeat_at is not None
                else row[6],
                total_event_count=row[7],
                unknown_count=row[8],
                invalid_amount_count=row[9],
                baseline_event_count=row[10],
                queue_lag_seconds=queue_row[0],
                dlq_count=queue_row[1],
                asia_qr_va_pending_age_seconds=pending_row[0],
                asia_refund_shape_b_pending_age_seconds=pending_row[1],
                india_non_terminal_age_seconds=pending_row[2],
                india_refund_created_age_seconds=pending_row[3],
                evidence_txn_ids=evidence,
            )
            for row in rows
        )


class PostgresRuleStateRepository:
    def __init__(
        self,
        connection: Any,
        lock_scope: Callable[[EvaluationScope], None],
    ) -> None:
        self.connection = connection
        self.lock_scope = lock_scope

    def list_for_scope(self, scope: EvaluationScope) -> tuple[RuleState, ...]:
        self.lock_scope(scope)
        rows = self.connection.execute(
            """
            SELECT rule_id, rule_version, consecutive_breaches,
                   consecutive_recoveries
            FROM health_rule_state
            WHERE region = %s AND tenant_scope = %s AND provider = %s
            ORDER BY rule_id, rule_version
            """,
            (scope.region.value, scope.tenant_scope, scope.provider),
        ).fetchall()
        return tuple(RuleState(*row) for row in rows)

    def save_all(
        self, scope: EvaluationScope, states: Sequence[RuleState]
    ) -> None:
        for state in states:
            self.connection.execute(
                """
                INSERT INTO health_rule_state (
                    region, tenant_scope, provider, rule_id, rule_version,
                    consecutive_breaches, consecutive_recoveries, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (
                    region, tenant_scope, provider, rule_id, rule_version
                ) DO UPDATE SET
                    consecutive_breaches = EXCLUDED.consecutive_breaches,
                    consecutive_recoveries = EXCLUDED.consecutive_recoveries,
                    updated_at = now()
                """,
                (
                    scope.region.value,
                    scope.tenant_scope,
                    scope.provider,
                    state.rule_id,
                    state.rule_version,
                    state.consecutive_breaches,
                    state.consecutive_recoveries,
                ),
            )


class PostgresAlertRepository:
    _SELECT = """
        SELECT alert_id, region, tenant_scope, provider, rule_id, rule_version,
               status, severity, health_impact, reason_code, reason_message,
               observed_value, comparison, threshold, sample_size,
               minimum_sample, window_size_minutes, evaluated_window_count,
               first_window_start, last_window_end, data_as_of,
               cooldown_seconds, runbook_ref, occurrence_count, opened_at, last_seen_at, last_evaluated_at,
               last_notification_attempt_at, last_notified_at, resolved_at
        FROM alert
    """

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def get_open(
        self,
        scope: EvaluationScope,
        *,
        rule_id: str,
        rule_version: int,
    ) -> AlertRecord | None:
        row = self.connection.execute(
            self._SELECT
            + """
              WHERE region = %s AND tenant_scope = %s AND provider = %s
                AND rule_id = %s AND rule_version = %s AND status = 'OPEN'
              FOR UPDATE
            """,
            (
                scope.region.value,
                scope.tenant_scope,
                scope.provider,
                rule_id,
                rule_version,
            ),
        ).fetchone()
        return self._to_domain(row) if row is not None else None

    def get(self, alert_id: str) -> AlertRecord:
        row = self.connection.execute(
            self._SELECT + " WHERE alert_id = %s", (alert_id,)
        ).fetchone()
        if row is None:
            raise KeyError(alert_id)
        return self._to_domain(row)

    def save(self, alert: AlertRecord) -> None:
        reason = alert.reason
        self.connection.execute(
            """
            INSERT INTO alert (
                alert_id, region, tenant_scope, provider, rule_id,
                rule_version, status, severity, health_impact, reason_code,
                reason_message, observed_value, comparison, threshold,
                sample_size, minimum_sample, window_size_minutes,
                evaluated_window_count, first_window_start, last_window_end,
                data_as_of, reasons, occurrence_count, cooldown_seconds,
                runbook_ref, opened_at, last_seen_at, last_evaluated_at,
                last_notification_attempt_at, last_notified_at, resolved_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (alert_id) DO UPDATE SET
                status = EXCLUDED.status,
                severity = EXCLUDED.severity,
                health_impact = EXCLUDED.health_impact,
                reason_code = EXCLUDED.reason_code,
                reason_message = EXCLUDED.reason_message,
                observed_value = EXCLUDED.observed_value,
                comparison = EXCLUDED.comparison,
                threshold = EXCLUDED.threshold,
                sample_size = EXCLUDED.sample_size,
                minimum_sample = EXCLUDED.minimum_sample,
                window_size_minutes = EXCLUDED.window_size_minutes,
                evaluated_window_count = EXCLUDED.evaluated_window_count,
                first_window_start = EXCLUDED.first_window_start,
                last_window_end = EXCLUDED.last_window_end,
                data_as_of = EXCLUDED.data_as_of,
                reasons = EXCLUDED.reasons,
                occurrence_count = EXCLUDED.occurrence_count,
                cooldown_seconds = EXCLUDED.cooldown_seconds,
                runbook_ref = EXCLUDED.runbook_ref,
                last_seen_at = EXCLUDED.last_seen_at,
                last_evaluated_at = EXCLUDED.last_evaluated_at,
                last_notification_attempt_at = EXCLUDED.last_notification_attempt_at,
                last_notified_at = EXCLUDED.last_notified_at,
                resolved_at = EXCLUDED.resolved_at,
                updated_at = now()
            """,
            (
                alert.alert_id,
                alert.scope.region.value,
                alert.scope.tenant_scope,
                alert.scope.provider,
                alert.rule_id,
                alert.rule_version,
                alert.status.value,
                alert.severity.value,
                reason.health_impact.value,
                reason.reason_code,
                reason.message,
                reason.observed_value,
                reason.comparison.value,
                reason.threshold,
                reason.sample_size,
                reason.minimum_sample,
                reason.window_size_minutes,
                reason.evaluated_window_count,
                reason.first_window_start,
                reason.last_window_end,
                reason.data_as_of,
                Jsonb([_reason_payload(reason)]),
                alert.occurrence_count,
                alert.cooldown_seconds,
                reason.runbook_ref,
                alert.opened_at,
                alert.last_seen_at,
                alert.last_evaluated_at,
                alert.last_notification_attempt_at,
                alert.last_notified_at,
                alert.resolved_at,
            ),
        )

    @staticmethod
    def _cooldown_seconds(alert: AlertRecord) -> int:
        # Cooldown belongs to the versioned rule snapshot. AlertRecord keeps the
        # reason only, so repositories receive it through this transient field
        # when present; zero remains safe for records built outside the use case.
        return int(getattr(alert, "cooldown_seconds", 0))

    @staticmethod
    def _to_domain(row: tuple[Any, ...]) -> AlertRecord:
        reason = HealthReason(
            rule_id=row[4],
            rule_version=row[5],
            evaluation_status=(
                EvaluationStatus.BREACH
                if row[6] == AlertStatus.OPEN.value
                else EvaluationStatus.PASS
            ),
            health_impact=HealthImpact(row[8]),
            reason_code=row[9],
            message=row[10],
            observed_value=row[11],
            comparison=Comparison(row[12]),
            threshold=row[13],
            sample_size=row[14],
            minimum_sample=row[15],
            window_size_minutes=row[16],
            evaluated_window_count=row[17],
            first_window_start=row[18],
            last_window_end=row[19],
            runbook_ref=row[22],
            data_as_of=row[20],
        )
        return AlertRecord(
            alert_id=str(row[0]),
            scope=EvaluationScope(
                region=Region(row[1]),
                tenant_scope=row[2],
                provider=row[3],
            ),
            rule_id=row[4],
            rule_version=row[5],
            status=AlertStatus(row[6]),
            severity=Severity(row[7]),
            reason=reason,
            occurrence_count=row[23],
            cooldown_seconds=row[21],
            opened_at=row[24],
            last_seen_at=row[25],
            last_evaluated_at=row[26],
            last_notification_attempt_at=row[27],
            last_notified_at=row[28],
            resolved_at=row[29],
        )


class PostgresAlertUnitOfWork:
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = _default_dsn(dsn)
        self.connection: Any | None = None
        self.alerts: PostgresAlertRepository
        self.rule_states: PostgresRuleStateRepository
        self._committed = False
        self._locked_scopes: set[EvaluationScope] = set()

    def _connect(self) -> Any:
        import psycopg

        return psycopg.connect(self.dsn)

    def __enter__(self) -> PostgresAlertUnitOfWork:
        self.connection = self._connect()
        self.connection.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            (os.getenv("HEALTH_WRITE_TIMEOUT", "5000ms"),),
        )
        self._committed = False
        self._locked_scopes = set()
        self.alerts = PostgresAlertRepository(self.connection)
        self.rule_states = PostgresRuleStateRepository(
            self.connection, self._lock_scope
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        if self.connection is None:
            return
        try:
            if not self._committed:
                self.connection.rollback()
        finally:
            self.connection.close()
            self.connection = None

    def _active(self) -> Any:
        if self.connection is None:
            raise RuntimeError("unit of work is not active")
        return self.connection

    def _lock_scope(self, scope: EvaluationScope) -> None:
        if scope in self._locked_scopes:
            return
        key = "\x1f".join(
            (scope.region.value, scope.tenant_scope, scope.provider)
        )
        self._active().execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (key,)
        )
        self._locked_scopes.add(scope)

    def evaluation_exists(
        self, scope: EvaluationScope, evaluated_at: datetime
    ) -> bool:
        self._lock_scope(scope)
        return self._active().execute(
            """
            SELECT 1 FROM health_evaluation_run
            WHERE region = %s AND tenant_scope = %s AND provider = %s
              AND evaluated_at = %s
            """,
            (
                scope.region.value,
                scope.tenant_scope,
                scope.provider,
                evaluated_at,
            ),
        ).fetchone() is not None

    def record_evaluation(
        self,
        evaluation: HealthEvaluation,
        transitions: Sequence[AlertTransition],
    ) -> None:
        scope = evaluation.scope
        self._active().execute(
            """
            INSERT INTO health_evaluation_run (
                evaluation_id, region, tenant_scope, provider, evaluated_at,
                data_as_of, health_status, rule_count, transition_count,
                reasons, evaluations
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                uuid4(),
                scope.region.value,
                scope.tenant_scope,
                scope.provider,
                evaluation.evaluated_at,
                evaluation.data_as_of,
                evaluation.status.value,
                len(evaluation.evaluations),
                len(transitions),
                Jsonb([_reason_payload(item) for item in evaluation.reasons]),
                Jsonb([
                    {
                        "rule_id": item.config.rule_id,
                        "rule_version": item.config.rule_version,
                        "status": item.status.value,
                        "consecutive_breaches": item.consecutive_breaches,
                        "consecutive_recoveries": item.consecutive_recoveries,
                        "breach_confirmed": item.breach_confirmed,
                        "recovery_confirmed": item.recovery_confirmed,
                        "reason": _reason_payload(item.reason),
                    }
                    for item in evaluation.evaluations
                ]),
            ),
        )

    def commit(self) -> None:
        self._active().commit()
        self._committed = True


class PostgresHealthScopeRepository:
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = _default_dsn(dsn)

    def list_scopes(self) -> tuple[EvaluationScope, ...]:
        import psycopg

        with psycopg.connect(self.dsn) as connection:
            connection.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (os.getenv("HEALTH_QUERY_TIMEOUT", "3000ms"),),
            )
            rows = connection.execute(
                """
                SELECT DISTINCT region, tenant_scope, provider
                FROM window_aggregate
                ORDER BY region, tenant_scope, provider
                """
            ).fetchall()
        return tuple(
            EvaluationScope(Region(row[0]), row[1], row[2]) for row in rows
        )


class PostgresHealthRunnerStatusRepository:
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = _default_dsn(dsn)

    def _connect(self) -> Any:
        import psycopg

        return psycopg.connect(self.dsn)

    def record_success(
        self,
        scope: EvaluationScope,
        *,
        run_at: datetime,
        data_as_of: datetime | None,
        freshness_sla_seconds: int,
        summary: dict[str, Any],
    ) -> None:
        lag = (
            max(0, int((run_at - data_as_of).total_seconds()))
            if data_as_of is not None
            else None
        )
        with self._connect() as connection:
            activity = connection.execute(
                """
                SELECT max(last_message_received_at), max(last_heartbeat_at)
                FROM source_activity
                WHERE region = %s AND tenant_scope = %s AND provider = %s
                """,
                (scope.region.value, scope.tenant_scope, scope.provider),
            ).fetchone()
            last_message_at, last_heartbeat_at = activity or (None, None)
            heartbeat_lag = (
                max(0, int((run_at - last_heartbeat_at).total_seconds()))
                if last_heartbeat_at is not None else None
            )
            transport_lag = (
                max(0, int((run_at - last_message_at).total_seconds()))
                if last_message_at is not None else None
            )
            status = classify_source_freshness(
                event_lag_seconds=lag,
                heartbeat_lag_seconds=heartbeat_lag,
                sla_seconds=freshness_sla_seconds,
            ).value
            summary = {
                **summary,
                "heartbeat_lag_seconds": heartbeat_lag,
                "transport_lag_seconds": transport_lag,
            }
            connection.execute(
                """
                INSERT INTO health_runner_status (
                    region, tenant_scope, provider, last_run_at,
                    last_success_at, data_as_of, freshness_lag_seconds,
                    freshness_sla_seconds, freshness_status,
                    consecutive_failures, last_error_code,
                    last_error_message, last_summary
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    0, NULL, NULL, %s
                )
                ON CONFLICT (region, tenant_scope, provider) DO UPDATE SET
                    last_run_at = EXCLUDED.last_run_at,
                    last_success_at = EXCLUDED.last_success_at,
                    data_as_of = EXCLUDED.data_as_of,
                    freshness_lag_seconds = EXCLUDED.freshness_lag_seconds,
                    freshness_sla_seconds = EXCLUDED.freshness_sla_seconds,
                    freshness_status = EXCLUDED.freshness_status,
                    consecutive_failures = 0,
                    last_error_code = NULL,
                    last_error_message = NULL,
                    last_summary = EXCLUDED.last_summary,
                    updated_at = now()
                """,
                (
                    scope.region.value,
                    scope.tenant_scope,
                    scope.provider,
                    run_at,
                    run_at,
                    data_as_of,
                    lag,
                    freshness_sla_seconds,
                    status,
                    Jsonb(summary),
                ),
            )

    def record_failure(
        self,
        scope: EvaluationScope,
        *,
        run_at: datetime,
        freshness_sla_seconds: int,
        error: BaseException,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO health_runner_status (
                    region, tenant_scope, provider, last_run_at,
                    freshness_sla_seconds, freshness_status,
                    consecutive_failures, last_error_code,
                    last_error_message, last_summary
                ) VALUES (%s, %s, %s, %s, %s, 'FAILED', 1, %s, %s, '{}'::jsonb)
                ON CONFLICT (region, tenant_scope, provider) DO UPDATE SET
                    last_run_at = EXCLUDED.last_run_at,
                    freshness_sla_seconds = EXCLUDED.freshness_sla_seconds,
                    freshness_status = 'FAILED',
                    consecutive_failures =
                        health_runner_status.consecutive_failures + 1,
                    last_error_code = EXCLUDED.last_error_code,
                    last_error_message = EXCLUDED.last_error_message,
                    updated_at = now()
                """,
                (
                    scope.region.value,
                    scope.tenant_scope,
                    scope.provider,
                    run_at,
                    freshness_sla_seconds,
                    type(error).__name__,
                    str(error)[:500],
                ),
            )

    def get(self, scope: EvaluationScope) -> HealthRunnerStatus:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT last_run_at, last_success_at, data_as_of,
                       freshness_lag_seconds, freshness_sla_seconds,
                       freshness_status, consecutive_failures,
                       last_error_code, last_error_message
                FROM health_runner_status
                WHERE region = %s AND tenant_scope = %s AND provider = %s
                """,
                (scope.region.value, scope.tenant_scope, scope.provider),
            ).fetchone()
        if row is None:
            raise KeyError(scope)
        return HealthRunnerStatus(
            scope=scope,
            last_run_at=row[0],
            last_success_at=row[1],
            data_as_of=row[2],
            freshness_lag_seconds=row[3],
            freshness_sla_seconds=row[4],
            freshness_status=FreshnessStatus(row[5]),
            consecutive_failures=row[6],
            last_error_code=row[7],
            last_error_message=row[8],
        )
