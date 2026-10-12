"""Persist provider-health rule state and evaluation runs.

Revision ID: 0012
Revises: 0011
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE health_rule_state (
            region text NOT NULL,
            tenant_scope text NOT NULL DEFAULT '',
            provider text NOT NULL,
            rule_id text NOT NULL,
            rule_version integer NOT NULL,
            consecutive_breaches integer NOT NULL DEFAULT 0,
            consecutive_recoveries integer NOT NULL DEFAULT 0,
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (
                region, tenant_scope, provider, rule_id, rule_version
            ),
            CONSTRAINT ck_health_rule_state_region
                CHECK (region IN ('VN', 'ASIA', 'INDIA')),
            CONSTRAINT ck_health_rule_state_asia_tenant
                CHECK (region <> 'ASIA' OR tenant_scope <> ''),
            CONSTRAINT ck_health_rule_state_values CHECK (
                btrim(provider) <> ''
                AND btrim(rule_id) <> ''
                AND rule_version > 0
                AND consecutive_breaches >= 0
                AND consecutive_recoveries >= 0
                AND NOT (
                    consecutive_breaches > 0
                    AND consecutive_recoveries > 0
                )
            )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE health_evaluation_run (
            evaluation_id uuid PRIMARY KEY,
            region text NOT NULL,
            tenant_scope text NOT NULL DEFAULT '',
            provider text NOT NULL,
            evaluated_at timestamptz NOT NULL,
            data_as_of timestamptz,
            health_status text NOT NULL,
            rule_count integer NOT NULL,
            transition_count integer NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_health_evaluation_run
                UNIQUE (region, tenant_scope, provider, evaluated_at),
            CONSTRAINT ck_health_evaluation_run_region
                CHECK (region IN ('VN', 'ASIA', 'INDIA')),
            CONSTRAINT ck_health_evaluation_run_asia_tenant
                CHECK (region <> 'ASIA' OR tenant_scope <> ''),
            CONSTRAINT ck_health_evaluation_run_status CHECK (
                health_status IN (
                    'HEALTHY', 'DEGRADED', 'UNHEALTHY', 'NO_DATA'
                )
            ),
            CONSTRAINT ck_health_evaluation_run_values CHECK (
                btrim(provider) <> ''
                AND rule_count > 0
                AND transition_count >= 0
                AND (data_as_of IS NULL OR data_as_of <= evaluated_at)
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_health_evaluation_run_scope_time
        ON health_evaluation_run (
            region, tenant_scope, provider, evaluated_at DESC
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE health_evaluation_run")
    op.execute("DROP TABLE health_rule_state")
