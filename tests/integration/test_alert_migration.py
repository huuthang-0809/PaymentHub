"""PostgreSQL integration tests for the alert lifecycle migration.

The configured database is reset. Run only against a disposable database.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 5, 10, 16, tzinfo=UTC)


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


def _downgrade(url: str, revision: str) -> None:
    previous_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        command.downgrade(Config("alembic.ini"), revision)
    finally:
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url


def _insert_alert(connection: psycopg.Connection, alert_id: str) -> None:
    connection.execute(
        """
        INSERT INTO alert (
            alert_id, region, tenant_scope, provider, rule_id, rule_version,
            status, severity, health_impact, reason_code, reason_message,
            observed_value, comparison, threshold, sample_size,
            minimum_sample, window_size_minutes, evaluated_window_count,
            first_window_start, last_window_end, data_as_of, reasons,
            occurrence_count, cooldown_seconds, runbook_ref, opened_at,
            last_seen_at, last_evaluated_at
        ) VALUES (
            %s, 'VN', '', 'ONEPAY', 'DEMO_LOW_SUCCESS_RATE_V1', 1,
            'OPEN', 'CRITICAL', 'UNHEALTHY', 'LOW_SUCCESS_RATE',
            'Success rate is below threshold', 0.5, 'LT', 0.8, 120,
            100, 5, 1, %s - interval '5 minutes', %s, %s,
            '[{"reason_code":"LOW_SUCCESS_RATE"}]'::jsonb,
            1, 900, 'fixture://low-success-rate', %s, %s, %s
        )
        """,
        (alert_id, NOW, NOW, NOW, NOW, NOW, NOW),
    )


def test_empty_database_upgrade_enforces_alert_lifecycle_constraints() -> None:
    url = _database_url()
    _reset_database(url)
    try:
        _upgrade(url, "head")
        with psycopg.connect(_psycopg_url(url)) as connection:
            revision = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0]
            assert revision == "0019"

            columns = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'alert'
                    """
                ).fetchall()
            }
            assert {
                "alert_id", "region", "tenant_scope", "provider",
                "rule_id", "rule_version", "status", "severity",
                "observed_value", "threshold", "sample_size", "reasons",
                "occurrence_count", "opened_at", "last_seen_at",
                "last_evaluated_at", "last_notified_at", "resolved_at",
                "cooldown_seconds", "runbook_ref",
            } <= columns

            first_id = str(uuid4())
            _insert_alert(connection, first_id)
            connection.commit()

            with pytest.raises(psycopg.errors.UniqueViolation):
                _insert_alert(connection, str(uuid4()))
            connection.rollback()

            connection.execute(
                """
                UPDATE alert
                SET status = 'RESOLVED', resolved_at = %s,
                    last_evaluated_at = %s, updated_at = %s
                WHERE alert_id = %s
                """,
                (NOW, NOW, NOW, first_id),
            )
            _insert_alert(connection, str(uuid4()))
            connection.commit()

            rows = connection.execute(
                """
                SELECT status, count(*) FROM alert
                GROUP BY status ORDER BY status
                """
            ).fetchall()
            assert rows == [("OPEN", 1), ("RESOLVED", 1)]

            indexes = dict(
                connection.execute(
                    """
                    SELECT indexname, indexdef FROM pg_indexes
                    WHERE schemaname = 'public' AND tablename = 'alert'
                    """
                ).fetchall()
            )
            assert "uq_alert_open_dedup" in indexes
            assert "WHERE (status = 'OPEN'::text)" in indexes["uq_alert_open_dedup"]
            assert "ix_alert_api_filter" in indexes
            assert "ix_alert_rule_history" in indexes
    finally:
        _reset_database(url)


def test_upgrade_from_0010_preserves_alert_data_and_downgrade_is_reversible() -> None:
    url = _database_url()
    _reset_database(url)
    try:
        _upgrade(url, "0010")
        alert_id = str(uuid4())

        with psycopg.connect(_psycopg_url(url)) as connection:
            _insert_alert(connection, alert_id)
            connection.commit()

        _upgrade(url, "head")
        with psycopg.connect(_psycopg_url(url)) as connection:
            alert = connection.execute(
                """
                SELECT status
                FROM alert
                WHERE alert_id = %s
                """,
                (alert_id,),
            ).fetchone()
            assert alert == ("OPEN",)

            assert connection.execute(
                "SELECT to_regclass('public.source_checkpoint')"
            ).fetchone()[0] == "source_checkpoint"

        _downgrade(url, "0010")
        with psycopg.connect(_psycopg_url(url)) as connection:
            assert connection.execute(
                "SELECT to_regclass('public.alert')"
            ).fetchone()[0] == "alert"

            assert connection.execute(
                "SELECT to_regclass('public.source_checkpoint')"
            ).fetchone()[0] is None

            alert_count = connection.execute(
                "SELECT count(*) FROM alert WHERE alert_id = %s",
                (alert_id,),
            ).fetchone()[0]
            assert alert_count == 1

            revision = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0]
            assert revision == "0010"
    finally:
        _reset_database(url)
