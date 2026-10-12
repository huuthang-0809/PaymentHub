"""Create provider-health alert lifecycle storage.

Revision ID: 0011
Revises: 0010
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE alert (
            alert_id uuid PRIMARY KEY,
            region text NOT NULL,
            tenant_scope text NOT NULL DEFAULT '',
            provider text NOT NULL,
            rule_id text NOT NULL,
            rule_version integer NOT NULL,
            status text NOT NULL,
            severity text NOT NULL,
            health_impact text NOT NULL,
            reason_code text NOT NULL,
            reason_message text NOT NULL,
            observed_value numeric,
            comparison text NOT NULL,
            threshold numeric NOT NULL,
            sample_size bigint,
            minimum_sample bigint,
            window_size_minutes smallint,
            evaluated_window_count integer NOT NULL,
            first_window_start timestamptz,
            last_window_end timestamptz,
            data_as_of timestamptz,
            reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
            occurrence_count bigint NOT NULL DEFAULT 1,
            cooldown_seconds integer NOT NULL,
            runbook_ref text NOT NULL,
            opened_at timestamptz NOT NULL,
            last_seen_at timestamptz NOT NULL,
            last_evaluated_at timestamptz NOT NULL,
            last_notification_attempt_at timestamptz,
            last_notified_at timestamptz,
            resolved_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_alert_region
                CHECK (region IN ('VN', 'ASIA', 'INDIA')),
            CONSTRAINT ck_alert_asia_tenant
                CHECK (region <> 'ASIA' OR tenant_scope <> ''),
            CONSTRAINT ck_alert_status
                CHECK (status IN ('OPEN', 'RESOLVED')),
            CONSTRAINT ck_alert_severity
                CHECK (severity IN ('WARNING', 'CRITICAL')),
            CONSTRAINT ck_alert_health_impact
                CHECK (health_impact IN ('DEGRADED', 'UNHEALTHY')),
            CONSTRAINT ck_alert_comparison
                CHECK (comparison IN ('LT', 'GTE', 'GT')),
            CONSTRAINT ck_alert_positive_values CHECK (
                rule_version > 0
                AND occurrence_count > 0
                AND evaluated_window_count >= 0
                AND cooldown_seconds >= 0
                AND (sample_size IS NULL OR sample_size >= 0)
                AND (minimum_sample IS NULL OR minimum_sample > 0)
                AND (window_size_minutes IS NULL OR window_size_minutes IN (1, 5, 15))
            ),
            CONSTRAINT ck_alert_nonempty_values CHECK (
                btrim(provider) <> ''
                AND btrim(rule_id) <> ''
                AND btrim(reason_code) <> ''
                AND btrim(reason_message) <> ''
                AND btrim(runbook_ref) <> ''
            ),
            CONSTRAINT ck_alert_window_range CHECK (
                first_window_start IS NULL
                OR last_window_end IS NULL
                OR last_window_end > first_window_start
            ),
            CONSTRAINT ck_alert_time_order CHECK (
                last_seen_at >= opened_at
                AND last_evaluated_at >= opened_at
                AND (data_as_of IS NULL OR data_as_of <= last_evaluated_at)
                AND (resolved_at IS NULL OR resolved_at >= opened_at)
                AND (last_notification_attempt_at IS NULL
                    OR last_notification_attempt_at >= opened_at)
                AND (last_notified_at IS NULL OR last_notified_at >= opened_at)
            ),
            CONSTRAINT ck_alert_resolution CHECK (
                (status = 'OPEN' AND resolved_at IS NULL)
                OR (status = 'RESOLVED' AND resolved_at IS NOT NULL)
            ),
            CONSTRAINT ck_alert_reasons_array
                CHECK (jsonb_typeof(reasons) = 'array')
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_alert_open_dedup
        ON alert (region, tenant_scope, provider, rule_id, rule_version)
        WHERE status = 'OPEN'
        """
    )
    op.execute(
        """
        CREATE INDEX ix_alert_api_filter
        ON alert (
            status, severity, region, tenant_scope, provider,
            opened_at DESC, alert_id DESC
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_alert_rule_history
        ON alert (
            rule_id, rule_version, region, tenant_scope, provider,
            opened_at DESC, alert_id DESC
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE alert")
