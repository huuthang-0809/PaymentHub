"""Persist arrival lateness and source heartbeat/activity."""

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE canonical_event
            ADD COLUMN received_at timestamptz,
            ADD COLUMN event_time_late boolean NOT NULL DEFAULT false,
            ADD COLUMN arrival_late boolean NOT NULL DEFAULT false,
            ADD COLUMN arrival_lag_seconds bigint,
            ADD CONSTRAINT ck_canonical_arrival_lag_nonnegative
                CHECK (arrival_lag_seconds IS NULL OR arrival_lag_seconds >= 0);

        CREATE TABLE source_activity (
            source_name text NOT NULL,
            region text NOT NULL,
            tenant_scope text NOT NULL DEFAULT '',
            provider text NOT NULL,
            last_message_received_at timestamptz NOT NULL,
            last_event_received_at timestamptz,
            last_event_time timestamptz,
            last_heartbeat_at timestamptz,
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (source_name, region, tenant_scope, provider)
        );

        ALTER TABLE health_runner_status
            DROP CONSTRAINT ck_health_runner_status_value;
        ALTER TABLE health_runner_status
            ADD CONSTRAINT ck_health_runner_status_value CHECK (
                btrim(provider) <> ''
                AND freshness_sla_seconds >= 0
                AND consecutive_failures >= 0
                AND (freshness_lag_seconds IS NULL OR freshness_lag_seconds >= 0)
                AND freshness_status IN (
                    'FRESH', 'IDLE', 'STALE', 'UNAVAILABLE', 'NO_DATA', 'FAILED'
                )
                AND (last_success_at IS NULL OR last_success_at <= last_run_at)
                AND (data_as_of IS NULL OR data_as_of <= last_run_at)
            );
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE health_runner_status
            DROP CONSTRAINT ck_health_runner_status_value;
        ALTER TABLE health_runner_status
            ADD CONSTRAINT ck_health_runner_status_value CHECK (
                btrim(provider) <> ''
                AND freshness_sla_seconds >= 0
                AND consecutive_failures >= 0
                AND (freshness_lag_seconds IS NULL OR freshness_lag_seconds >= 0)
                AND freshness_status IN ('FRESH', 'STALE', 'NO_DATA', 'FAILED')
                AND (last_success_at IS NULL OR last_success_at <= last_run_at)
                AND (data_as_of IS NULL OR data_as_of <= last_run_at)
            );
        DROP TABLE source_activity;
        ALTER TABLE canonical_event
            DROP CONSTRAINT ck_canonical_arrival_lag_nonnegative,
            DROP COLUMN arrival_lag_seconds,
            DROP COLUMN arrival_late,
            DROP COLUMN event_time_late,
            DROP COLUMN received_at;
        """
    )
