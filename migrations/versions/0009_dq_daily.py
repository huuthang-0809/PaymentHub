"""Store idempotent daily data-quality results for completed runs."""

from alembic import op


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE dq_daily (
            business_date date NOT NULL,
            run_id uuid NOT NULL,
            job_run_id uuid NOT NULL,
            region varchar(5) NOT NULL,
            tenant_id text,
            provider varchar(64) NOT NULL,
            rule_code varchar(64) NOT NULL,
            rule_version integer NOT NULL,
            severity varchar(8) NOT NULL,
            status varchar(6) NOT NULL,
            evaluated_count integer NOT NULL,
            affected_count integer NOT NULL,
            affected_result_keys integer[] NOT NULL DEFAULT '{}',
            evaluated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (business_date, run_id, rule_code, rule_version),
            CONSTRAINT fk_dq_daily_run
                FOREIGN KEY (run_id, job_run_id, business_date)
                REFERENCES recon_run(run_id, job_run_id, business_date)
                ON DELETE CASCADE,
            CONSTRAINT ck_dq_daily_rule_version CHECK (rule_version > 0),
            CONSTRAINT ck_dq_daily_severity
                CHECK (severity IN ('WARNING', 'CRITICAL')),
            CONSTRAINT ck_dq_daily_status
                CHECK (status IN ('PASSED', 'FAILED')),
            CONSTRAINT ck_dq_daily_counts CHECK (
                evaluated_count >= 0
                AND affected_count >= 0
                AND affected_count <= evaluated_count
                AND cardinality(affected_result_keys) = affected_count
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_dq_daily_scope_date
        ON dq_daily (business_date DESC, region, tenant_id, provider)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE dq_daily")
