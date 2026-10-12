"""Persist freshness watchdog and health runner status.

Revision ID: 0013
Revises: 0012
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE health_runner_status (
            region text NOT NULL,
            tenant_scope text NOT NULL DEFAULT '',
            provider text NOT NULL,
            last_run_at timestamptz NOT NULL,
            last_success_at timestamptz,
            data_as_of timestamptz,
            freshness_lag_seconds bigint,
            freshness_sla_seconds integer NOT NULL,
            freshness_status text NOT NULL,
            consecutive_failures integer NOT NULL DEFAULT 0,
            last_error_code text,
            last_error_message text,
            last_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (region, tenant_scope, provider),
            CONSTRAINT ck_health_runner_status_region
                CHECK (region IN ('VN', 'ASIA', 'INDIA')),
            CONSTRAINT ck_health_runner_status_asia_tenant
                CHECK (region <> 'ASIA' OR tenant_scope <> ''),
            CONSTRAINT ck_health_runner_status_value CHECK (
                btrim(provider) <> ''
                AND freshness_sla_seconds >= 0
                AND consecutive_failures >= 0
                AND (freshness_lag_seconds IS NULL
                    OR freshness_lag_seconds >= 0)
                AND freshness_status IN (
                    'FRESH', 'STALE', 'NO_DATA', 'FAILED'
                )
                AND (last_success_at IS NULL
                    OR last_success_at <= last_run_at)
                AND (data_as_of IS NULL OR data_as_of <= last_run_at)
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_health_runner_status_freshness
        ON health_runner_status (
            freshness_status, consecutive_failures DESC, last_run_at DESC
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE health_runner_status")
