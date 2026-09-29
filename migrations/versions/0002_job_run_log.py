from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE job_run_log (
            job_run_id uuid PRIMARY KEY,
            job_name text NOT NULL,
            business_date date NOT NULL,
            region text,
            tenant_id text,
            provider text,
            status text NOT NULL DEFAULT 'RUNNING',
            started_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            error_code text,
            error_message text,
            metrics jsonb NOT NULL DEFAULT '{}'::jsonb,
            CHECK (finished_at IS NULL OR finished_at >= started_at)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_job_run_log_scope_date
        ON job_run_log (job_name, business_date, region, tenant_id, provider)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE job_run_log")
