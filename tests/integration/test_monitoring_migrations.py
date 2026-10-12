"""PostgreSQL integration coverage for the monitoring schema migrations.

The configured database is reset. Run only against a disposable database with:

    PAYMENTHUB_RUN_DB_INTEGRATION=1 \
    TEST_DATABASE_URL=postgresql+psycopg://... \
    uv run pytest tests/integration/test_monitoring_migrations.py -q
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url


pytestmark = pytest.mark.integration


def _database_url() -> str:
    if os.getenv("PAYMENTHUB_RUN_DB_INTEGRATION") != "1":
        pytest.skip("set PAYMENTHUB_RUN_DB_INTEGRATION=1 to run destructive DB tests")
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL must point to a disposable PostgreSQL database")
    return url


def _psycopg_url(url: str) -> str:
    parsed = make_url(url)
    return parsed.set(drivername="postgresql").render_as_string(hide_password=False)


def _reset_database(url: str) -> None:
    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        connection.execute("DROP SCHEMA IF EXISTS public CASCADE")
        connection.execute("CREATE SCHEMA public")


def _upgrade(url: str, revision: str) -> None:
    previous_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        command.upgrade(Config("alembic.ini"), revision)
    finally:
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url


def _seed_0014_schema(url: str, event_date) -> tuple[str, str]:
    first_event_id = str(uuid4())
    second_event_id = str(uuid4())
    other_tenant_event_id = str(uuid4())
    fourth_event_id = str(uuid4())
    event_ids = (
        first_event_id,
        second_event_id,
        other_tenant_event_id,
        fourth_event_id,
    )
    with psycopg.connect(_psycopg_url(url)) as connection:
        for event_id in event_ids:
            connection.execute(
                """
                INSERT INTO ingest_message (event_id, event_type, payload_hash)
                VALUES (%s, 'PAYMENT_UPDATED', %s)
                """,
                (event_id, f"hash-{event_id}"),
            )
        for event_id, tenant_id, status in (
            (first_event_id, "tenant-a", "PENDING"),
            (second_event_id, "tenant-a", "SUCCESS"),
            (other_tenant_event_id, "tenant-b", "PENDING"),
            (fourth_event_id, "tenant-b", "SUCCESS"),
        ):
            connection.execute(
                """
                INSERT INTO canonical_event (
                    event_date, event_id, region, tenant_id, txn_id, txn_type,
                    provider, status, amount_minor, currency, updated_at,
                    created_at
                ) VALUES (
                    %s, %s, 'VN', %s, 'same-txn', 'PAYMENT',
                    'provider-a', %s, 1000, 'VND', %s, %s
                )
                """,
                (event_date, event_id, tenant_id, status, NOW, NOW),
            )
    return first_event_id, second_event_id


NOW = datetime.now(UTC).replace(microsecond=0)


def test_upgrade_from_empty_and_existing_schema_preserves_event_grain() -> None:
    url = _database_url()
    _reset_database(url)
    try:
        # Empty database -> the canonical alignment schema.
        _upgrade(url, "0014")
        first_event_id, _ = _seed_0014_schema(url, NOW.date())

        # Existing schema/data -> the new head.
        _upgrade(url, "head")
        _upgrade(url, "head")  # Re-running head is a no-op.

        with psycopg.connect(_psycopg_url(url)) as connection:
            rows = connection.execute(
                """
                SELECT tenant_scope, status
                FROM canonical_event
                WHERE txn_id = 'same-txn'
                ORDER BY tenant_scope, status
                """
            ).fetchall()
            assert rows == [
                ("tenant-a", "PENDING"),
                ("tenant-a", "SUCCESS"),
                ("tenant-b", "PENDING"),
                ("tenant-b", "SUCCESS"),
            ]

            duplicate_event_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO ingest_message (event_id, event_type, payload_hash)
                VALUES (%s, 'PAYMENT_UPDATED', 'duplicate-hash')
                """,
                (duplicate_event_id,),
            )
            with pytest.raises(psycopg.errors.UniqueViolation):
                connection.execute(
                    """
                    INSERT INTO canonical_event (
                        event_date, event_id, idempotency_key,
                        event_identity_kind, event_time, region, tenant_id,
                        txn_id, txn_type, provider, status, created_at, job_run_id
                    ) VALUES (
                        %s, %s, %s, 'TRUSTED', %s, 'VN', 'tenant-a',
                        'different-txn', 'PAYMENT', 'provider-a', 'SUCCESS', %s,
                        '00000000-0000-0000-0000-000000000001'
                    )
                    """,
                    (
                        NOW.date(),
                        duplicate_event_id,
                        f"trusted:{first_event_id}",
                        NOW,
                        NOW,
                    ),
                )
            connection.rollback()

        with psycopg.connect(_psycopg_url(url)) as connection:
            window_start = NOW.replace(minute=0, second=0)
            for size in (1, 5, 15):
                connection.execute(
                    """
                    INSERT INTO window_aggregate (
                        window_size_minutes, window_start, window_end,
                        region, tenant_scope, provider, txn_type, instrument,
                        channel_raw, currency, total_count,
                        operation_event_count, success_count, failed_count,
                        pending_count, unknown_count, success_rate, failed_rate,
                        unknown_rate, monetary_count, monetary_amount_minor,
                        invalid_amount_count, missing_currency_count,
                        source_event_count, last_event_at, job_run_id
                    ) VALUES (
                        %s, %s, %s + make_interval(mins => %s),
                        'VN', 'tenant-a', 'provider-a', 'PAYMENT', 'EWALLET',
                        'APP', 'VND', 1, 1, 1, 0, 0, 0, 1, 0, 0,
                        1, 1000, 0, 0, 1, %s,
                        '00000000-0000-0000-0000-000000000001'
                    )
                    """,
                    (size, window_start, window_start, size, NOW),
                )
            count = connection.execute(
                """
                SELECT count(*) FROM window_aggregate
                WHERE window_start = %s AND tenant_scope = 'tenant-a'
                """,
                (window_start,),
            ).fetchone()[0]
            assert count == 3

            revision = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0]
            assert revision == "0017"
    finally:
        _reset_database(url)
