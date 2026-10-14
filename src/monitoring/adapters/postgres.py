import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from psycopg.types.json import Jsonb

from canonical.domain import CanonicalTransaction
from monitoring.application import (
    MonitoringUnavailable,
    TransactionQueryRepository,
    WindowMetric,
    WindowMetricPage,
    WindowMetricQuery,
)
from monitoring.domain import (
    CanonicalEvent, EventTiming, WatermarkPolicy, WindowAggregate
)
from shared_kernel import AmountStatus, CanonicalStatus, Region, TxnType
from shared_kernel.enums import Instrument


_AGGREGATION_LOCK_ID = 0x50415948


def _dsn(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _json_default(value: object) -> str:
    return str(value)


def _payload_hash(event: CanonicalEvent) -> str:
    transaction = event.transaction
    payload = {
        "event_id": event.event_id,
        "region": transaction.region.value,
        "tenant_id": transaction.tenant_id,
        "txn_id": transaction.txn_id,
        "txn_type": transaction.txn_type.value,
        "provider": transaction.provider,
        "status": transaction.status.value,
        "amount_status": transaction.amount_status.value,
        "created_at": transaction.created_at.isoformat(),
        "updated_at": (
            transaction.updated_at.isoformat() if transaction.updated_at else None
        ),
        "amount_minor": transaction.amount_minor,
        "currency": transaction.currency,
        "raw": transaction.raw,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=_json_default
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _identity(event: CanonicalEvent) -> tuple[date, str, UUID]:
    event_date = event.event_time.astimezone(UTC).date()
    idempotency_key = f"trusted:{event.event_id}"
    database_id = uuid5(NAMESPACE_URL, f"{event_date.isoformat()}|{idempotency_key}")
    return event_date, idempotency_key, database_id


class PostgresTransactionRepository(TransactionQueryRepository):
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = _dsn(
            dsn
            or os.getenv(
                "DATABASE_URL",
                "postgresql://user:password@postgres:5432/paymenthub",
            )
        )

    def _connect(self) -> Any:
        import psycopg

        return psycopg.connect(self.dsn)

    def is_ready(self) -> bool:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    return cursor.fetchone() == (1,)
        except Exception:
            return False

    def list_transactions(
        self,
        *,
        region: Region | None,
        status: CanonicalStatus | None,
        limit: int,
    ) -> Sequence[CanonicalTransaction]:
        where: list[str] = []
        parameters: list[Any] = []
        if region is not None:
            where.append("region = %s")
            parameters.append(region.value)
        if status is not None:
            where.append("status = %s")
            parameters.append(status.value)
        predicate = f"WHERE {' AND '.join(where)}" if where else ""
        query = f"""
            SELECT region, tenant_id, txn_id, txn_type, provider, status,
                   amount_status, created_at, updated_at, amount_minor, currency,
                   is_terminal, is_money_movement, is_reconcilable,
                   is_deleted, is_test_data
              FROM canonical_event
              {predicate}
             ORDER BY updated_at DESC NULLS LAST, created_at DESC
             LIMIT %s
        """
        parameters.append(limit)
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, parameters)
                return tuple(self._to_domain(row) for row in cursor.fetchall())

    @staticmethod
    def _to_domain(row: tuple[Any, ...]) -> CanonicalTransaction:
        return CanonicalTransaction(
            region=Region(row[0]),
            tenant_id=row[1],
            txn_id=row[2],
            txn_type=TxnType(row[3]),
            provider=row[4],
            status=CanonicalStatus(row[5]),
            amount_status=AmountStatus(row[6]),
            created_at=row[7],
            updated_at=row[8],
            amount_minor=int(row[9]) if row[9] is not None else None,
            currency=row[10],
            is_terminal=row[11],
            is_money_movement=row[12],
            is_reconcilable=row[13],
            is_deleted=row[14],
            is_test_data=row[15],
        )

    def list_window_metrics(self, query: WindowMetricQuery) -> WindowMetricPage:
        import psycopg

        where = ["window_size_minutes = %s"]
        parameters: list[Any] = [query.window_size]
        if query.region is not None:
            where.append("region = %s")
            parameters.append(query.region.value)
        if query.tenant_id is not None:
            where.append("tenant_scope = %s")
            parameters.append(query.tenant_id)
        if query.start is not None:
            where.append("window_start >= %s")
            parameters.append(query.start)
        if query.end is not None:
            where.append("window_end <= %s")
            parameters.append(query.end)
        predicate = " AND ".join(where)
        parameters.extend((query.limit + 1, query.offset))
        sql = f"""
            SELECT window_size_minutes, window_start, window_end, region,
                   tenant_scope, provider, txn_type, instrument, channel_raw,
                   currency, total_count, operation_event_count, success_count,
                   failed_count, pending_count, unknown_count, success_rate,
                   failed_rate, unknown_rate, monetary_count,
                   monetary_amount_minor, invalid_amount_count,
                   missing_currency_count, parse_error_count, late_event_count,
                   non_terminal_count,
                   pending_age_lt_5m_count, pending_age_5_to_15m_count,
                   pending_age_15_to_60m_count, pending_age_gte_60m_count,
                   oldest_pending_age_seconds, freshness_lag_seconds,
                   source_event_count, last_event_at,
                   computed_at, max(computed_at) OVER () AS data_as_of
            FROM window_aggregate
            WHERE {predicate}
            ORDER BY window_start DESC, region, tenant_scope, provider,
                     txn_type, instrument, channel_raw, currency
            LIMIT %s OFFSET %s
        """
        try:
            with self._connect() as connection:
                connection.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (os.getenv("MONITORING_QUERY_TIMEOUT", "2000ms"),),
                )
                rows = connection.execute(sql, parameters).fetchall()
                data_as_of = rows[0][35] if rows else connection.execute(
                    """
                    SELECT max(finished_at) FROM job_run_log
                    WHERE job_name = 'monitoring-canonical-events'
                      AND status = 'COMPLETED'
                    """
                ).fetchone()[0]
        except (
            psycopg.errors.QueryCanceled,
            psycopg.OperationalError,
            psycopg.InterfaceError,
        ) as error:
            raise MonitoringUnavailable("Monitoring database is unavailable") from error

        has_more = len(rows) > query.limit
        rows = rows[: query.limit]
        items = tuple(self._to_window_metric(row) for row in rows)
        return WindowMetricPage(
            items=items,
            data_as_of=data_as_of,
            next_offset=query.offset + query.limit if has_more else None,
        )

    @staticmethod
    def _to_window_metric(row: tuple[Any, ...]) -> WindowMetric:
        return WindowMetric(
            window_size_minutes=row[0],
            window_start=row[1],
            window_end=row[2],
            region=Region(row[3]),
            tenant_id=row[4] or None,
            provider=row[5],
            txn_type=row[6],
            instrument=row[7] or None,
            channel_raw=row[8] or None,
            currency=row[9].strip() if row[9] else None,
            total_event_count=row[10],
            operation_event_count=row[11],
            success_count=row[12],
            failed_count=row[13],
            pending_count=row[14],
            unknown_count=row[15],
            success_rate=row[16],
            failed_rate=row[17],
            unknown_rate=row[18],
            monetary_count=row[19],
            monetary_amount_minor=int(row[20]),
            invalid_amount_count=row[21],
            missing_currency_count=row[22],
            parse_error_count=row[23],
            late_event_count=row[24],
            non_terminal_count=row[25],
            pending_age_lt_5m_count=row[26],
            pending_age_5_to_15m_count=row[27],
            pending_age_15_to_60m_count=row[28],
            pending_age_gte_60m_count=row[29],
            oldest_pending_age_seconds=row[30],
            freshness_lag_seconds=row[31],
            source_event_count=row[32],
            last_event_at=row[33],
            computed_at=row[34],
        )


class PostgresCanonicalEventRepository:
    def __init__(
        self,
        connection: Any,
        job_run_id: UUID,
        aggregate_from: datetime | None = None,
    ) -> None:
        self.connection = connection
        self.job_run_id = job_run_id
        self.aggregate_from = aggregate_from

    def contains(self, event: CanonicalEvent) -> bool:
        event_date, idempotency_key, database_id = _identity(event)
        stored_hash = self.connection.execute(
            "SELECT payload_hash FROM ingest_message WHERE event_id = %s",
            (database_id,),
        ).fetchone()
        if stored_hash is None:
            return False
        if stored_hash[0] != _payload_hash(event):
            raise ValueError(
                f"event_id collision with different payload: {event.event_id}"
            )
        return self.connection.execute(
            """
            SELECT 1 FROM canonical_event
            WHERE event_date = %s AND idempotency_key = %s
            """,
            (event_date, idempotency_key),
        ).fetchone() is not None

    def insert_once(self, event: CanonicalEvent) -> bool:
        event_date, idempotency_key, database_id = _identity(event)
        payload_hash = _payload_hash(event)
        transaction = event.transaction
        inserted_message = self.connection.execute(
            """
            INSERT INTO ingest_message (event_id, event_type, payload_hash)
            VALUES (%s, 'CANONICAL_UPDATE', %s)
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id
            """,
            (database_id, payload_hash),
        ).fetchone()
        if inserted_message is None:
            stored_hash = self.connection.execute(
                "SELECT payload_hash FROM ingest_message WHERE event_id = %s",
                (database_id,),
            ).fetchone()
            if stored_hash is None or stored_hash[0] != payload_hash:
                raise ValueError(
                    f"event_id collision with different payload: {event.event_id}"
                )

        raw = {
            "payload": transaction.raw,
            "__monitoring__": {
                "instrument": (
                    transaction.instrument.value if transaction.instrument else None
                ),
                "channel_raw": transaction.channel_raw,
                "parse_error": event.parse_error,
                "is_late": event.is_late,
                "event_time_late": event.event_time_late,
                "arrival_late": event.arrival_late,
                "arrival_lag_seconds": event.arrival_lag_seconds,
            },
        }
        inserted_event = self.connection.execute(
            """
            INSERT INTO canonical_event (
                event_date, event_id, idempotency_key, event_identity_kind,
                event_time, region, tenant_id, txn_id, txn_type, provider,
                status, provider_txn_id, provider_ref_id, merchant_ref,
                refund_of, amount_minor, currency, amount_status, created_at,
                updated_at, is_terminal, is_money_movement, is_reconcilable,
                is_deleted, is_test_data, raw, job_run_id, received_at,
                event_time_late, arrival_late, arrival_lag_seconds
            ) VALUES (
                %s, %s, %s, 'TRUSTED', %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s
            )
            ON CONFLICT (event_date, idempotency_key) DO NOTHING
            RETURNING event_id
            """,
            (
                event_date,
                database_id,
                idempotency_key,
                event.event_time,
                transaction.region.value,
                transaction.tenant_id,
                transaction.txn_id,
                transaction.txn_type.value,
                transaction.provider,
                transaction.status.value,
                transaction.provider_txn_id,
                transaction.provider_ref_id,
                transaction.merchant_ref,
                transaction.refund_of,
                transaction.amount_minor,
                transaction.currency,
                transaction.amount_status.value,
                transaction.created_at,
                transaction.updated_at,
                transaction.is_terminal,
                transaction.is_money_movement,
                transaction.is_reconcilable,
                transaction.is_deleted,
                transaction.is_test_data,
                Jsonb(raw, dumps=lambda value: json.dumps(value, default=_json_default)),
                self.job_run_id,
                event.received_at,
                event.event_time_late,
                event.arrival_late,
                event.arrival_lag_seconds,
            ),
        ).fetchone()
        return inserted_event is not None

    def list_events(self) -> tuple[CanonicalEvent, ...]:
        rows = self.connection.execute(
            """
            SELECT event_date, idempotency_key, region, tenant_id, txn_id,
                   txn_type, provider, status, amount_status, created_at,
                   updated_at, amount_minor, currency, is_terminal,
                   is_money_movement, is_reconcilable, is_deleted,
                   is_test_data, provider_txn_id, provider_ref_id,
                   merchant_ref, refund_of, raw, received_at,
                   event_time_late, arrival_late, arrival_lag_seconds
            FROM canonical_event
            WHERE (%s IS NULL OR event_time >= %s)
            ORDER BY event_date, event_time, idempotency_key
            """,
            (self.aggregate_from, self.aggregate_from),
        ).fetchall()
        return tuple(self._to_event(row) for row in rows)

    @staticmethod
    def _to_event(row: tuple[Any, ...]) -> CanonicalEvent:
        raw = row[22] if isinstance(row[22], dict) else {}
        monitoring = raw.get("__monitoring__", {})
        payload = raw.get("payload", raw)
        instrument_value = monitoring.get("instrument")
        transaction = CanonicalTransaction(
            region=Region(row[2]),
            tenant_id=row[3],
            txn_id=row[4],
            txn_type=TxnType(row[5]),
            provider=row[6],
            status=CanonicalStatus(row[7]),
            amount_status=AmountStatus(row[8]),
            created_at=row[9],
            updated_at=row[10],
            amount_minor=int(row[11]) if row[11] is not None else None,
            currency=row[12].strip() if row[12] else None,
            is_terminal=row[13],
            is_money_movement=row[14],
            is_reconcilable=row[15],
            is_deleted=row[16],
            is_test_data=row[17],
            provider_txn_id=row[18],
            provider_ref_id=row[19],
            merchant_ref=row[20],
            refund_of=row[21],
            instrument=Instrument(instrument_value) if instrument_value else None,
            channel_raw=monitoring.get("channel_raw"),
            raw=payload if isinstance(payload, dict) else {},
        )
        return CanonicalEvent(
            event_id=f"{row[0].isoformat()}:{row[1]}",
            transaction=transaction,
            parse_error=bool(
                monitoring.get(
                    "parse_error", transaction.amount_status is AmountStatus.INVALID
                )
            ),
            is_late=bool(monitoring.get("is_late", False)),
            received_at=row[23],
            event_time_late=bool(row[24]),
            arrival_late=bool(row[25]),
            arrival_lag_seconds=row[26],
        )


class PostgresWindowAggregateRepository:
    def __init__(self, connection: Any, job_run_id: UUID) -> None:
        self.connection = connection
        self.job_run_id = job_run_id

    def upsert_all(self, aggregates: Sequence[WindowAggregate]) -> None:
        for aggregate in aggregates:
            key = aggregate.key
            self.connection.execute(
                """
                INSERT INTO window_aggregate (
                    window_size_minutes, window_start, window_end, region,
                    tenant_scope, provider, txn_type, instrument, channel_raw,
                    currency, total_count, operation_event_count, success_count,
                    failed_count, pending_count, unknown_count, success_rate,
                    failed_rate, unknown_rate, monetary_count,
                    monetary_amount_minor, invalid_amount_count,
                    missing_currency_count, parse_error_count, late_event_count,
                   non_terminal_count,
                   pending_age_lt_5m_count, pending_age_5_to_15m_count,
                   pending_age_15_to_60m_count, pending_age_gte_60m_count,
                   oldest_pending_age_seconds, freshness_lag_seconds,
                   source_event_count, last_event_at,
                    computed_at, job_run_id
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (
                    window_size_minutes, window_start, region, tenant_scope,
                    provider, txn_type, instrument, channel_raw, currency
                ) DO UPDATE SET
                    window_end = EXCLUDED.window_end,
                    total_count = EXCLUDED.total_count,
                    operation_event_count = EXCLUDED.operation_event_count,
                    success_count = EXCLUDED.success_count,
                    failed_count = EXCLUDED.failed_count,
                    pending_count = EXCLUDED.pending_count,
                    unknown_count = EXCLUDED.unknown_count,
                    success_rate = EXCLUDED.success_rate,
                    failed_rate = EXCLUDED.failed_rate,
                    unknown_rate = EXCLUDED.unknown_rate,
                    monetary_count = EXCLUDED.monetary_count,
                    monetary_amount_minor = EXCLUDED.monetary_amount_minor,
                    invalid_amount_count = EXCLUDED.invalid_amount_count,
                    missing_currency_count = EXCLUDED.missing_currency_count,
                    parse_error_count = EXCLUDED.parse_error_count,
                    late_event_count = EXCLUDED.late_event_count,
                    non_terminal_count = EXCLUDED.non_terminal_count,
                    pending_age_lt_5m_count = EXCLUDED.pending_age_lt_5m_count,
                    pending_age_5_to_15m_count = EXCLUDED.pending_age_5_to_15m_count,
                    pending_age_15_to_60m_count = EXCLUDED.pending_age_15_to_60m_count,
                    pending_age_gte_60m_count = EXCLUDED.pending_age_gte_60m_count,
                    oldest_pending_age_seconds = EXCLUDED.oldest_pending_age_seconds,
                    freshness_lag_seconds = EXCLUDED.freshness_lag_seconds,
                    source_event_count = EXCLUDED.source_event_count,
                    last_event_at = EXCLUDED.last_event_at,
                    computed_at = EXCLUDED.computed_at,
                    job_run_id = EXCLUDED.job_run_id
                """,
                (
                    key.window.size_minutes,
                    key.window.start,
                    key.window.end,
                    key.region.value,
                    key.tenant_scope,
                    key.provider,
                    key.txn_type.value,
                    key.instrument,
                    key.channel_raw,
                    key.currency,
                    aggregate.total_event_count,
                    aggregate.operation_event_count,
                    aggregate.success_count,
                    aggregate.failed_count,
                    aggregate.pending_count,
                    aggregate.unknown_count,
                    aggregate.success_rate,
                    aggregate.failed_rate,
                    aggregate.unknown_rate,
                    aggregate.monetary_count,
                    aggregate.monetary_amount_minor,
                    aggregate.invalid_amount_count,
                    aggregate.missing_currency_count,
                    aggregate.parse_error_count,
                    aggregate.late_event_count,
                    aggregate.non_terminal_count,
                    aggregate.pending_age_lt_5m_count,
                    aggregate.pending_age_5_to_15m_count,
                    aggregate.pending_age_15_to_60m_count,
                    aggregate.pending_age_gte_60m_count,
                    aggregate.oldest_pending_age_seconds,
                    aggregate.freshness_lag_seconds,
                    aggregate.total_event_count,
                    aggregate.last_event_at,
                    aggregate.computed_at,
                    self.job_run_id,
                ),
            )


class PostgresEventUnitOfWork:
    def __init__(
        self,
        dsn: str | None = None,
        *,
        job_name: str = "monitoring-canonical-events",
        source_name: str = "replay",
        business_date: date | None = None,
        aggregate_from: datetime | None = None,
    ) -> None:
        self.dsn = _dsn(
            dsn
            or os.getenv(
                "DATABASE_URL",
                "postgresql://user:password@postgres:5432/paymenthub",
            )
        )
        self.job_name = job_name
        self.source_name = source_name
        self.business_date = business_date or datetime.now(UTC).date()
        self.aggregate_from = aggregate_from
        self.job_run_id = uuid4()
        self.connection: Any | None = None
        self.events: PostgresCanonicalEventRepository
        self.aggregates: PostgresWindowAggregateRepository
        self._summary: dict[str, Any] = {}
        self._baseline_watermarks: dict[tuple[str, str], datetime] = {}
        self._staged_watermarks: dict[tuple[str, str], datetime] = {}
        self._committed = False
        self._data_from: datetime | None = None
        self._data_to: datetime | None = None

    def _connect(self) -> Any:
        import psycopg

        return psycopg.connect(self.dsn)

    def _set_job_status(
        self,
        status: str,
        *,
        metrics: Mapping[str, Any] | None = None,
        error: BaseException | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE job_run_log
                SET status = %s, finished_at = now(), metrics = %s,
                    error_code = %s, error_message = %s
                WHERE job_run_id = %s
                """,
                (
                    status,
                    Jsonb(dict(metrics or {})),
                    type(error).__name__ if error else None,
                    str(error)[:500] if error else None,
                    self.job_run_id,
                ),
            )

    def __enter__(self) -> "PostgresEventUnitOfWork":
        self.job_run_id = uuid4()
        with self._connect() as job_connection:
            job_connection.execute(
                """
                INSERT INTO job_run_log (
                    job_run_id, job_name, business_date, status, source_name
                ) VALUES (%s, %s, %s, 'RUNNING', %s)
                """,
                (
                    self.job_run_id,
                    self.job_name,
                    self.business_date,
                    self.source_name,
                ),
            )
        try:
            self.connection = self._connect()
            self.connection.execute(
                "SELECT pg_advisory_xact_lock(%s)", (_AGGREGATION_LOCK_ID,)
            )
            self.events = PostgresCanonicalEventRepository(
                self.connection, self.job_run_id, self.aggregate_from
            )
            self.aggregates = PostgresWindowAggregateRepository(
                self.connection, self.job_run_id
            )
            rows = self.connection.execute(
                """
                SELECT region, tenant_scope, watermark
                FROM source_checkpoint WHERE source_name = %s
                """,
                (self.source_name,),
            ).fetchall()
            self._baseline_watermarks = {
                (row[0], row[1]): row[2] for row in rows
            }
            self._staged_watermarks = {}
            self._data_from = None
            self._data_to = None
            self._committed = False
            return self
        except BaseException as error:
            self._set_job_status("FAILED", error=error)
            raise

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
                error = exc_value or RuntimeError("unit of work exited without commit")
                failure_metrics = dict(self._summary)
                failure_metrics["attempted_inserted"] = failure_metrics.get(
                    "inserted", 0
                )
                failure_metrics["inserted"] = 0
                failure_metrics["aggregate_count"] = 0
                failure_metrics["rolled_back"] = True
                self._set_job_status(
                    "FAILED", metrics=failure_metrics, error=error
                )
        finally:
            self.connection.close()
            self.connection = None

    @staticmethod
    def _scope(event: CanonicalEvent) -> tuple[str, str]:
        transaction = event.transaction
        return transaction.region.value, transaction.tenant_id or ""

    def stage_watermark(self, event: CanonicalEvent) -> None:
        scope = self._scope(event)
        event_time = event.event_time
        if self._data_from is None or event_time < self._data_from:
            self._data_from = event_time
        if self._data_to is None or event_time > self._data_to:
            self._data_to = event_time
        current = self._staged_watermarks.get(scope)
        if current is None or event.event_time > current:
            self._staged_watermarks[scope] = event.event_time

    def classify_timing(
        self, event: CanonicalEvent, policy: WatermarkPolicy
    ) -> EventTiming:
        scope = self._scope(event)
        timing = policy.classify(
            event.event_time, self._baseline_watermarks.get(scope)
        )
        self.stage_watermark(event)
        return timing

    def audit_expired(self, event: CanonicalEvent) -> bool:
        if self.connection is None:
            raise RuntimeError("unit of work is not active")
        scope = self._scope(event)
        watermark = self._baseline_watermarks.get(scope)
        if watermark is None:
            raise RuntimeError("expired event requires an existing watermark")
        row = self.connection.execute(
            """
            INSERT INTO late_event_audit (
                source_name, event_id, region, tenant_scope, txn_id,
                event_time, watermark, classification, reason_code, job_run_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'EXPIRED',
                      'OUTSIDE_RETENTION', %s)
            ON CONFLICT (source_name, event_id) DO NOTHING
            RETURNING event_id
            """,
            (
                self.source_name, event.event_id, scope[0], scope[1],
                event.transaction.txn_id, event.event_time, watermark,
                self.job_run_id,
            ),
        ).fetchone()
        return row is not None

    def record_event_activity(self, event: CanonicalEvent) -> None:
        if self.connection is None:
            raise RuntimeError("unit of work is not active")
        transaction = event.transaction
        received_at = event.received_at or datetime.now(UTC)
        self.connection.execute(
            """
            INSERT INTO source_activity (
                source_name, region, tenant_scope, provider,
                last_message_received_at, last_event_received_at, last_event_time
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_name, region, tenant_scope, provider) DO UPDATE SET
                last_message_received_at = GREATEST(
                    source_activity.last_message_received_at,
                    EXCLUDED.last_message_received_at
                ),
                last_event_received_at = GREATEST(
                    source_activity.last_event_received_at,
                    EXCLUDED.last_event_received_at
                ),
                last_event_time = GREATEST(
                    source_activity.last_event_time, EXCLUDED.last_event_time
                ),
                updated_at = now()
            """,
            (
                self.source_name, transaction.region.value,
                transaction.tenant_id or "", transaction.provider,
                received_at, received_at, event.event_time,
            ),
        )

    def persist_checkpoint(self) -> None:
        if not self._committed:
            raise RuntimeError("checkpoint requires a committed unit of work")
        with self._connect() as connection:
            for (region, tenant_scope), watermark in self._staged_watermarks.items():
                connection.execute(
                    """
                    INSERT INTO source_checkpoint (
                        source_name, region, tenant_scope, watermark,
                        source_position
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (source_name, region, tenant_scope) DO UPDATE SET
                        watermark = GREATEST(
                            source_checkpoint.watermark, EXCLUDED.watermark
                        ),
                        source_position = CASE
                            WHEN EXCLUDED.watermark > source_checkpoint.watermark
                            THEN EXCLUDED.source_position
                            ELSE source_checkpoint.source_position
                        END,
                        updated_at = now()
                    """,
                    (
                        self.source_name, region, tenant_scope, watermark,
                        Jsonb({"watermark": watermark.isoformat()}),
                    ),
                )

    def record_summary(self, summary: Mapping[str, Any]) -> None:
        self._summary = dict(summary)

    def commit(self) -> None:
        if self.connection is None:
            raise RuntimeError("unit of work is not active")
        self.connection.execute(
            """
            UPDATE job_run_log
            SET status = 'COMPLETED', finished_at = now(), metrics = %s,
                source_checkpoint = %s, data_from = %s, data_to = %s,
                error_code = NULL, error_message = NULL
            WHERE job_run_id = %s
            """,
            (
                Jsonb(self._summary),
                Jsonb({
                    f"{region}:{tenant_scope}": watermark.isoformat()
                    for (region, tenant_scope), watermark
                    in self._staged_watermarks.items()
                }),
                self._data_from,
                self._data_to,
                self.job_run_id,
            ),
        )
        self.connection.commit()
        self._committed = True
