from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE recon_result (
            result_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            run_id uuid NOT NULL,
            job_run_id uuid NOT NULL,
            result_key text NOT NULL,
            result_type text NOT NULL,
            region text NOT NULL,
            tenant_id text,
            provider text NOT NULL,
            txn_type text,
            system_txn_id text,
            system_amount_minor numeric(38, 0),
            system_currency text,
            provider_txn_id text,
            provider_amount_minor numeric(38, 0),
            provider_currency text,
            delta_amount numeric(38, 0),
            source_file text,
            source_row_number integer,
            reason text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_recon_result_run
                FOREIGN KEY (run_id, job_run_id)
                REFERENCES recon_run(run_id, job_run_id)
                ON DELETE CASCADE,
            CONSTRAINT uq_recon_result_key UNIQUE (run_id, result_key),
            CHECK (result_type IN (
                'MATCHED',
                'AMOUNT_MISMATCH',
                'CURRENCY_MISMATCH',
                'MISSING_IN_SYSTEM',
                'MISSING_IN_PROVIDER',
                'DUPLICATE',
                'FUZZY_MATCHED',
                'NOT_RECONCILABLE',
                'INVALID_DATA'
            )),
            CHECK (source_row_number IS NULL OR source_row_number > 0)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_recon_result_run_type
        ON recon_result (run_id, result_type)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_recon_result_attention
        ON recon_result (run_id, region, tenant_id, provider, result_type)
        WHERE result_type NOT IN ('MATCHED', 'FUZZY_MATCHED')
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE recon_result")
