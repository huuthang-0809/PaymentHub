import hashlib
import json
from dataclasses import asdict
from datetime import timedelta
from typing import Any

from psycopg import Connection, sql
from psycopg_pool import ConnectionPool

from ingestion.application.events import IngestEvent, PaymentUpdated

def _hash(event: IngestEvent) -> str:
    return hashlib.sha256(
        json.dumps(asdict(event), sort_keys=True, default=str).encode # Event -> dict -> bytes -> SHA-256 -> hash hexadecimal
    ).hexdigest()

class PostgreIngestRepository:
    def __init__(self, pool: ConnectionPool[Any]) -> None:
        self.pool = pool

    @staticmethod
    def _ensure_partition(conn: Connection[Any], day: Any) -> None:
        name = f"canonical_event_{day:%y%m%d}"
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (name,))
        conn.execute(
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {} PARTITION OF canonical_event FOR VALUES FROM ({}) TO ({})"
            ).format(sql.Identifier(name), sql.Literal(day), sql.Literal(day + timedelta(days=1)))
        )
