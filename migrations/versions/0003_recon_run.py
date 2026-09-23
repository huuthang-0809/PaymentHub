from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE recon_run (
            run_id uuid PRIMARY KEY,
            job_run_id uuid NOT NULL REFERENCES job_run_log(job_run_id),
            business_date date NOT NULL,
            region text NOT NULL,
            tenant_id text,
            provider text NOT NULL,
            source_file text NOT NULL,
            file_checksum text NOT NULL,
            business_timezone text NOT NULL,
            cutoff_time time NOT NULL,
            allowed_lateness_seconds integer NOT NULL DEFAULT 0,
            status text NOT NULL DEFAULT 'PENDING',
            started_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (run_id, job_run_id),
            CONSTRAINT uq_recon_run_input
                UNIQUE (business_date, provider, file_checksum),
            CHECK (allowed_lateness_seconds >= 0),
            CHECK (finished_at IS NULL OR finished_at >= started_at)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_recon_run_scope_date
        ON recon_run (business_date, region, tenant_id, provider)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE recon_run")
