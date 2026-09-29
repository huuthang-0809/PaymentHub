from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE provider_txn (
            provider_row_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            run_id uuid NOT NULL,
            job_run_id uuid NOT NULL,
            region text NOT NULL,
            tenant_id text,
            provider text NOT NULL,
            txn_type text NOT NULL,
            provider_txn_id text,
            provider_ref_id text,
            merchant_ref text,
            merchant_id text,
            amount_minor numeric(38, 0) NOT NULL,
            currency text NOT NULL,
            status text NOT NULL,
            transaction_time timestamptz NOT NULL,
            late_arriving boolean NOT NULL DEFAULT false,
            source_file text NOT NULL,
            source_row_number integer NOT NULL,
            raw jsonb NOT NULL DEFAULT '{}'::jsonb,
            ingested_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_provider_txn_run
                FOREIGN KEY (run_id, job_run_id)
                REFERENCES recon_run(run_id, job_run_id)
                ON DELETE CASCADE,
            CONSTRAINT uq_provider_txn_source_row
                UNIQUE (run_id, source_file, source_row_number),
            CHECK (source_row_number > 0),
            CHECK (amount_minor >= 0)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_provider_txn_scope_time
        ON provider_txn (
            region, tenant_id, provider, currency, transaction_time
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_provider_txn_provider_id
        ON provider_txn (region, tenant_id, provider, currency, provider_txn_id)
        WHERE provider_txn_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX ix_provider_txn_transaction_time_brin
        ON provider_txn USING brin (transaction_time)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE provider_txn")
