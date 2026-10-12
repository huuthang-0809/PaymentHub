"""Small durable PostgreSQL queue for monitoring work.

The adapter deliberately exposes explicit ACK/fail/reclaim operations so a worker
can commit business data before acknowledging a message. `SKIP LOCKED` permits
multiple workers without double-claiming the same ready row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from sqlalchemy.engine import make_url


def _dsn(value: str) -> str:
    parsed = make_url(value)
    return parsed.set(drivername="postgresql").render_as_string(hide_password=False)


@dataclass(frozen=True, slots=True)
class QueueMessage:
    message_id: UUID
    idempotency_key: str
    payload: Mapping[str, Any]
    attempts: int
    max_attempts: int


class PostgresMonitoringQueue:
    def __init__(self, dsn: str) -> None:
        self.dsn = _dsn(dsn)

    def _connect(self):
        import psycopg
        return psycopg.connect(self.dsn)

    def enqueue(
        self, idempotency_key: str, payload: Mapping[str, Any], *, max_attempts: int = 3
    ) -> tuple[UUID, bool]:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        message_id = uuid4()
        with self._connect() as connection:
            row = connection.execute(
                """
                INSERT INTO monitoring_queue_message (
                    message_id, idempotency_key, payload, max_attempts
                ) VALUES (%s, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING message_id
                """,
                (message_id, idempotency_key, Jsonb(dict(payload)), max_attempts),
            ).fetchone()
            if row is not None:
                return row[0], True
            existing = connection.execute(
                "SELECT message_id FROM monitoring_queue_message WHERE idempotency_key = %s",
                (idempotency_key,),
            ).fetchone()
            return existing[0], False

    def claim(self, *, now: datetime | None = None) -> QueueMessage | None:
        now = now or datetime.now(UTC)
        with self._connect() as connection:
            row = connection.execute(
                """
                WITH candidate AS (
                    SELECT message_id FROM monitoring_queue_message
                    WHERE status = 'READY' AND available_at <= %s
                    ORDER BY available_at, created_at
                    FOR UPDATE SKIP LOCKED LIMIT 1
                )
                UPDATE monitoring_queue_message AS message
                SET status = 'PROCESSING', attempts = attempts + 1,
                    claimed_at = %s, last_error_code = NULL,
                    last_error_class = NULL
                FROM candidate
                WHERE message.message_id = candidate.message_id
                RETURNING message.message_id, message.idempotency_key,
                          message.payload, message.attempts, message.max_attempts
                """,
                (now, now),
            ).fetchone()
        return QueueMessage(*row) if row else None

    def ack(self, message_id: UUID, *, now: datetime | None = None) -> None:
        with self._connect() as connection:
            row = connection.execute(
                """
                UPDATE monitoring_queue_message
                SET status = 'COMPLETED', finished_at = %s
                WHERE message_id = %s AND status = 'PROCESSING'
                RETURNING message_id
                """,
                (now or datetime.now(UTC), message_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("message is not processing")

    def fail(
        self,
        message_id: UUID,
        error: BaseException,
        *,
        transient: bool,
        retry_delay: timedelta = timedelta(0),
        now: datetime | None = None,
    ) -> str:
        now = now or datetime.now(UTC)
        with self._connect() as connection:
            row = connection.execute(
                """
                UPDATE monitoring_queue_message
                SET status = CASE
                        WHEN %s AND attempts < max_attempts THEN 'READY'
                        ELSE 'DEAD_LETTER'
                    END,
                    available_at = CASE
                        WHEN %s AND attempts < max_attempts THEN %s
                        ELSE available_at
                    END,
                    finished_at = CASE
                        WHEN %s AND attempts < max_attempts THEN NULL ELSE %s
                    END,
                    last_error_code = %s,
                    last_error_class = %s
                WHERE message_id = %s AND status = 'PROCESSING'
                RETURNING status
                """,
                (
                    transient, transient, now + retry_delay, transient, now,
                    type(error).__name__, "TRANSIENT" if transient else "PERMANENT",
                    message_id,
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("message is not processing")
            return row[0]

    def reclaim_stale(
        self, *, older_than: datetime, retry_delay: timedelta = timedelta(0)
    ) -> int:
        with self._connect() as connection:
            rows = connection.execute(
                """
                UPDATE monitoring_queue_message
                SET status = CASE WHEN attempts < max_attempts
                                  THEN 'READY' ELSE 'DEAD_LETTER' END,
                    available_at = %s + %s,
                    finished_at = CASE WHEN attempts < max_attempts
                                       THEN NULL ELSE %s END,
                    last_error_code = 'WorkerLost',
                    last_error_class = 'TRANSIENT'
                WHERE status = 'PROCESSING' AND claimed_at < %s
                RETURNING message_id
                """,
                (older_than, retry_delay, older_than, older_than),
            ).fetchall()
            return len(rows)
