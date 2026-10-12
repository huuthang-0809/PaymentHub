"""PostgreSQL transaction and concurrency tests for provider health alerts."""

from __future__ import annotations

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from monitoring.adapters.http import create_app
from monitoring.adapters.memory import (
    InMemoryRuleConfigRepository,
    RecordingNotificationPort,
)
from monitoring.adapters.postgres import PostgresTransactionRepository
from monitoring.adapters.postgres_health import (
    PostgresAlertUnitOfWork,
    PostgresHealthMetricSource,
    PostgresHealthRunnerStatusRepository,
)
from bootstrap.health_evaluation import main as health_evaluation_main
from monitoring.adapters.postgres_health_query import PostgresHealthQueryRepository
from monitoring.application import EvaluateProviderHealth
from monitoring.domain import (
    AlertAction,
    Comparison,
    EvaluationScope,
    HealthImpact,
    HealthMetric,
    RuleConfig,
    FreshnessStatus,
    Severity,
)
from shared_kernel import Region

pytestmark = pytest.mark.integration
FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "monitoring" / "health-alerts"


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


def _reset_and_migrate(url: str) -> None:
    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        connection.execute("DROP SCHEMA IF EXISTS public CASCADE")
        connection.execute("CREATE SCHEMA public")
    previous_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    try:
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url


@pytest.fixture
def postgres_url() -> str:
    url = _database_url()
    _reset_and_migrate(url)
    yield url
    with psycopg.connect(_psycopg_url(url), autocommit=True) as connection:
        connection.execute("DROP SCHEMA IF EXISTS public CASCADE")
        connection.execute("CREATE SCHEMA public")


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _scope(source: dict) -> EvaluationScope:
    item = source["scopes"]["onepay-vn"]
    return EvaluationScope(
        region=Region(item["region"]),
        tenant_scope=item["tenant_scope"],
        provider=item["provider"],
    )


def _configs() -> tuple[RuleConfig, ...]:
    return tuple(
        RuleConfig(
            rule_id=item["rule_id"],
            rule_version=item["rule_version"],
            metric=HealthMetric(item["metric"]),
            comparison=Comparison(item["comparison"]),
            threshold=Decimal(str(item["threshold"])),
            minimum_sample=item["minimum_sample"],
            window_size_minutes=item["window_size_minutes"],
            lookback_windows=item["lookback_windows"],
            breach_windows=item["breach_windows"],
            recovery_windows=item["recovery_windows"],
            health_impact=HealthImpact(item["health_impact"]),
            severity=Severity(item["severity"]),
            cooldown_seconds=item["cooldown_seconds"],
            runbook_ref=item["runbook_ref"],
        )
        for item in _load("demo-rule-config.json")["rules"]
    )


def _seed_cycle(url: str, source: dict, cycle: dict) -> None:
    rows = [item for item in cycle["windows"] if item["scope"] == "onepay-vn"]
    with psycopg.connect(_psycopg_url(url)) as connection:
        for item in rows:
            operation_count = item["operation_event_count"]
            success_count = item["success_count"]
            failed_count = item["failed_count"]
            connection.execute(
                """
                INSERT INTO window_aggregate (
                    window_size_minutes, window_start, window_end, region,
                    tenant_scope, provider, txn_type, instrument, channel_raw,
                    currency, total_count, operation_event_count, success_count,
                    failed_count, pending_count, unknown_count, success_rate,
                    failed_rate, unknown_rate, monetary_count,
                    monetary_amount_minor, invalid_amount_count,
                    missing_currency_count, parse_error_count, late_event_count,
                    non_terminal_count, pending_age_lt_5m_count,
                    pending_age_5_to_15m_count,
                    pending_age_15_to_60m_count,
                    pending_age_gte_60m_count, freshness_lag_seconds,
                    source_event_count, last_event_at, computed_at, job_run_id
                ) VALUES (
                    5, %s, %s, 'VN', '', 'ONEPAY', 'PAYMENT', 'CARD',
                    'card', 'VND', %s, %s, %s, %s, 0, 0,
                    %s, %s, 0, 0, 0, 0, 0, 0, 0,
                    0, 0, 0, 0, 0, 70, %s, %s, %s,
                    '00000000-0000-0000-0000-000000000001'
                )
                """,
                (
                    _timestamp(item["window_start"]),
                    _timestamp(item["window_end"]),
                    operation_count,
                    operation_count,
                    success_count,
                    failed_count,
                    Decimal(success_count) / Decimal(operation_count),
                    Decimal(failed_count) / Decimal(operation_count),
                    operation_count,
                    _timestamp(item["data_as_of"]),
                    _timestamp(cycle["evaluated_at"]),
                ),
            )


def _execute(
    url: str,
    scope: EvaluationScope,
    evaluated_at: datetime,
    notifications: RecordingNotificationPort,
    *,
    unit_of_work: PostgresAlertUnitOfWork | None = None,
):
    return EvaluateProviderHealth(
        PostgresHealthMetricSource(url),
        InMemoryRuleConfigRepository(_configs()),
        unit_of_work or PostgresAlertUnitOfWork(url),
        notifications,
    ).execute(scope, evaluated_at=evaluated_at)


def test_postgres_open_update_resolve_and_replay_are_idempotent(
    postgres_url: str,
) -> None:
    source = _load("window-series.json")
    scope = _scope(source)
    notifications = RecordingNotificationPort()
    outputs = []

    for cycle in source["cycles"]:
        _seed_cycle(postgres_url, source, cycle)
        outputs.append(
            _execute(
                postgres_url,
                scope,
                _timestamp(cycle["evaluated_at"]),
                notifications,
            )
        )

    replay = _execute(
        postgres_url,
        scope,
        _timestamp(source["cycles"][-1]["evaluated_at"]),
        notifications,
    )

    assert [item.action for item in outputs[2].transitions] == [
        AlertAction.OPEN,
        AlertAction.OPEN,
    ]
    assert [item.action for item in outputs[3].transitions] == [
        AlertAction.UPDATE,
        AlertAction.UPDATE,
    ]
    assert [item.action for item in outputs[5].transitions] == [
        AlertAction.RESOLVE,
        AlertAction.RESOLVE,
    ]
    assert replay.transitions == ()
    assert len(notifications.delivered) == 4

    with psycopg.connect(_psycopg_url(postgres_url)) as connection:
        alerts = connection.execute(
            """
            SELECT status, occurrence_count, cooldown_seconds,
                   reasons->0->>'reason_code'
            FROM alert ORDER BY rule_id
            """
        ).fetchall()
        run_count = connection.execute(
            "SELECT count(*) FROM health_evaluation_run"
        ).fetchone()[0]
        state_count = connection.execute(
            "SELECT count(*) FROM health_rule_state"
        ).fetchone()[0]
    assert len(alerts) == 2
    assert {row[0] for row in alerts} == {"RESOLVED"}
    assert {row[1] for row in alerts} == {2}
    assert {row[2] for row in alerts} == {900}
    assert all(row[3] for row in alerts)
    assert run_count == 6
    assert state_count == 3


def test_postgres_failure_rolls_back_state_alert_and_run(
    postgres_url: str,
) -> None:
    source = _load("window-series.json")
    scope = _scope(source)
    notifications = RecordingNotificationPort()
    for cycle in source["cycles"][:2]:
        _seed_cycle(postgres_url, source, cycle)
        _execute(
            postgres_url,
            scope,
            _timestamp(cycle["evaluated_at"]),
            notifications,
        )

    class FailingCommitUnitOfWork(PostgresAlertUnitOfWork):
        def commit(self) -> None:
            raise RuntimeError("injected health commit failure")

    open_cycle = source["cycles"][2]
    _seed_cycle(postgres_url, source, open_cycle)
    with pytest.raises(RuntimeError, match="injected health commit failure"):
        _execute(
            postgres_url,
            scope,
            _timestamp(open_cycle["evaluated_at"]),
            notifications,
            unit_of_work=FailingCommitUnitOfWork(postgres_url),
        )

    with psycopg.connect(_psycopg_url(postgres_url)) as connection:
        assert connection.execute("SELECT count(*) FROM alert").fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM health_evaluation_run"
        ).fetchone()[0] == 2
        rate_states = connection.execute(
            """
            SELECT consecutive_breaches FROM health_rule_state
            WHERE rule_id IN (
                'DEMO_LOW_SUCCESS_RATE_V1', 'DEMO_HIGH_FAILURE_RATE_V1'
            ) ORDER BY rule_id
            """
        ).fetchall()
    assert rate_states == [(1,), (1,)]


def test_concurrent_same_cycle_creates_one_open_per_rule(
    postgres_url: str,
) -> None:
    source = _load("window-series.json")
    scope = _scope(source)
    for cycle in source["cycles"][:2]:
        _seed_cycle(postgres_url, source, cycle)
        _execute(
            postgres_url,
            scope,
            _timestamp(cycle["evaluated_at"]),
            RecordingNotificationPort(),
        )

    open_cycle = source["cycles"][2]
    _seed_cycle(postgres_url, source, open_cycle)
    evaluated_at = _timestamp(open_cycle["evaluated_at"])
    barrier = Barrier(2)

    def evaluate() -> int:
        barrier.wait()
        output = _execute(
            postgres_url,
            scope,
            evaluated_at,
            RecordingNotificationPort(),
        )
        return len(output.transitions)

    with ThreadPoolExecutor(max_workers=2) as executor:
        transition_counts = list(executor.map(lambda _: evaluate(), range(2)))

    assert sorted(transition_counts) == [0, 2]
    with psycopg.connect(_psycopg_url(postgres_url)) as connection:
        alerts = connection.execute(
            """
            SELECT status, occurrence_count FROM alert ORDER BY rule_id
            """
        ).fetchall()
        run_count = connection.execute(
            "SELECT count(*) FROM health_evaluation_run"
        ).fetchone()[0]
    assert alerts == [("OPEN", 1), ("OPEN", 1)]
    assert run_count == 3


def test_notification_failure_happens_after_postgres_commit(
    postgres_url: str,
) -> None:
    source = _load("window-series.json")
    scope = _scope(source)
    for cycle in source["cycles"][:2]:
        _seed_cycle(postgres_url, source, cycle)
        _execute(
            postgres_url,
            scope,
            _timestamp(cycle["evaluated_at"]),
            RecordingNotificationPort(),
        )

    open_cycle = source["cycles"][2]
    _seed_cycle(postgres_url, source, open_cycle)
    class FailingCommittedNotification:
        def __init__(self) -> None:
            self.attempts = 0

        def send(self, alert, transition) -> None:
            self.attempts += 1
            with psycopg.connect(_psycopg_url(postgres_url)) as connection:
                visible = connection.execute(
                    "SELECT count(*) FROM alert WHERE alert_id = %s",
                    (alert.alert_id,),
                ).fetchone()[0]
            assert visible == 1
            raise RuntimeError("simulated notification failure")

    notifications = FailingCommittedNotification()
    output = _execute(
        postgres_url,
        scope,
        _timestamp(open_cycle["evaluated_at"]),
        notifications,
    )

    assert output.notification_failures == 2
    assert notifications.attempts == 2
    with psycopg.connect(_psycopg_url(postgres_url)) as connection:
        committed = connection.execute(
            "SELECT count(*) FROM alert WHERE status = 'OPEN'"
        ).fetchone()[0]
        runs = connection.execute(
            "SELECT count(*) FROM health_evaluation_run"
        ).fetchone()[0]
    assert committed == 2
    assert runs == 3


def test_one_shot_runner_evaluates_no_data_scope_and_replay_is_noop(
    postgres_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    as_of = "2026-10-05T10:31:00Z"
    args = [
        "--database-url", postgres_url,
        "--rules-file", str(Path(__file__).parents[2] / "config" / "health-rules.demo.json"),
        "--scope", "INDIA::RAZORPAY",
        "--as-of", as_of,
        "--freshness-sla-seconds", "600",
    ]

    assert health_evaluation_main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["evaluated_scopes"] == 1
    assert first["evaluated_rules"] == 3
    assert first["opened"] == 0
    assert first["scopes"][0]["health_status"] == "NO_DATA"
    assert first["scopes"][0]["data_as_of"] is None

    assert health_evaluation_main(args) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["skipped"] == 1
    assert replay["opened"] == 0

    scope = EvaluationScope(Region.INDIA, "", "RAZORPAY")
    status = PostgresHealthRunnerStatusRepository(postgres_url).get(scope)
    assert status.freshness_status is FreshnessStatus.NO_DATA
    assert status.data_as_of is None
    assert status.consecutive_failures == 0
    with psycopg.connect(_psycopg_url(postgres_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM health_evaluation_run"
        ).fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM alert").fetchone()[0] == 0


def test_freshness_watchdog_tracks_failures_and_success_reset(
    postgres_url: str,
) -> None:
    scope = EvaluationScope(Region.VN, "", "ONEPAY")
    repository = PostgresHealthRunnerStatusRepository(postgres_url)
    error = RuntimeError("synthetic runner failure")

    repository.record_failure(
        scope, run_at=_timestamp("2026-10-05T10:10:00Z"),
        freshness_sla_seconds=600, error=error,
    )
    repository.record_failure(
        scope, run_at=_timestamp("2026-10-05T10:11:00Z"),
        freshness_sla_seconds=600, error=error,
    )
    failed = repository.get(scope)
    assert failed.freshness_status is FreshnessStatus.FAILED
    assert failed.consecutive_failures == 2
    assert failed.last_error_code == "RuntimeError"

    repository.record_success(
        scope,
        run_at=_timestamp("2026-10-05T10:12:00Z"),
        data_as_of=_timestamp("2026-10-05T10:11:50Z"),
        freshness_sla_seconds=600,
        summary={"status": "HEALTHY"},
    )
    recovered = repository.get(scope)
    assert recovered.freshness_status is FreshnessStatus.FRESH
    assert recovered.freshness_lag_seconds == 10
    assert recovered.consecutive_failures == 0
    assert recovered.last_error_code is None


def test_health_alert_freshness_api_matches_committed_postgres(
    postgres_url: str,
) -> None:
    source = _load("window-series.json")
    scope = _scope(source)
    output = None
    for cycle in source["cycles"][:3]:
        _seed_cycle(postgres_url, source, cycle)
        output = _execute(
            postgres_url,
            scope,
            _timestamp(cycle["evaluated_at"]),
            RecordingNotificationPort(),
        )
    assert output is not None
    PostgresHealthRunnerStatusRepository(postgres_url).record_success(
        scope,
        run_at=output.evaluation.evaluated_at,
        data_as_of=output.evaluation.data_as_of,
        freshness_sla_seconds=600,
        summary={"status": output.evaluation.status.value},
    )

    app = create_app(
        PostgresTransactionRepository(postgres_url),
        PostgresHealthQueryRepository(postgres_url),
        cursor_secret="integration-cursor-secret",
    )

    async def get(path: str):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://integration"
        ) as client:
            return await client.get(path)

    health_response = asyncio.run(
        get("/health/providers?region=VN&provider=ONEPAY")
    )
    alert_response = asyncio.run(get("/alerts?status=OPEN&limit=1"))
    freshness_response = asyncio.run(
        get("/_meta/freshness?region=VN&provider=ONEPAY")
    )

    assert health_response.status_code == 200
    health = health_response.json()
    assert health["meta"]["count"] == 1
    assert health["data"][0]["status"] == "UNHEALTHY"
    assert len(health["data"][0]["evaluations"]) == 3
    assert len(health["data"][0]["open_alert_ids"]) == 2

    assert alert_response.status_code == 200
    alerts = alert_response.json()
    assert alerts["meta"]["count"] == 1
    assert alerts["meta"]["next_cursor"] is not None
    assert alerts["data"][0]["status"] == "OPEN"
    assert "raw" not in alerts["data"][0]

    assert freshness_response.status_code == 200
    freshness = freshness_response.json()
    assert freshness["meta"]["count"] == 1
    assert freshness["data"][0]["sla_status"] == "FRESH"
    assert freshness["data"][0]["lag_seconds"] == 70

    with psycopg.connect(_psycopg_url(postgres_url)) as connection:
        sql_counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM alert WHERE status = 'OPEN'),
                (SELECT count(*) FROM health_evaluation_run),
                (SELECT count(*) FROM health_runner_status)
            """
        ).fetchone()
    assert sql_counts == (2, 3, 1)
