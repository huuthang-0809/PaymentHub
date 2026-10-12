"""Add event identity, aggregate grain, and processing lineage."""

from alembic import op


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE job_run_log
            ADD COLUMN source_name text,
            ADD COLUMN source_checkpoint jsonb NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN data_from timestamptz,
            ADD COLUMN data_to timestamptz,
            ADD CONSTRAINT ck_job_run_log_data_range
                CHECK (data_to IS NULL OR data_from IS NULL OR data_to >= data_from)
        """
    )

    op.execute(
        """
        ALTER TABLE canonical_event
            ADD COLUMN idempotency_key text,
            ADD COLUMN event_identity_kind text NOT NULL DEFAULT 'TRUSTED',
            ADD COLUMN event_time timestamptz,
            ADD COLUMN tenant_scope text
                GENERATED ALWAYS AS (COALESCE(tenant_id, '')) STORED,
            ADD COLUMN source_name text,
            ADD COLUMN source_partition text,
            ADD COLUMN source_position text,
            ADD COLUMN ingested_at timestamptz NOT NULL DEFAULT now(),
            ADD COLUMN job_run_id uuid REFERENCES job_run_log(job_run_id)
        """
    )
    op.execute(
        """
        UPDATE canonical_event
        SET idempotency_key = 'trusted:' || event_id::text,
            event_time = COALESCE(updated_at, created_at)
        """
    )
    op.execute(
        """
        ALTER TABLE canonical_event
            ALTER COLUMN idempotency_key SET NOT NULL,
            ALTER COLUMN event_time SET NOT NULL,
            ADD CONSTRAINT ck_canonical_event_identity_kind
                CHECK (event_identity_kind IN ('TRUSTED', 'FALLBACK')),
            ADD CONSTRAINT ck_canonical_event_idempotency_key
                CHECK (
                    (event_identity_kind = 'TRUSTED' AND idempotency_key LIKE 'trusted:%')
                    OR
                    (event_identity_kind = 'FALLBACK' AND idempotency_key LIKE 'fallback:%')
                ),
            ADD CONSTRAINT ck_canonical_event_event_date
                CHECK ((event_time AT TIME ZONE 'UTC')::date = event_date) NOT VALID,
            ADD CONSTRAINT uq_canonical_event_date_idempotency
                UNIQUE (event_date, idempotency_key)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_canonical_event_transaction_history
        ON canonical_event (
            event_date, region, tenant_scope, txn_id, event_time DESC
        )
        """
    )

    op.execute("ALTER TABLE window_aggregate RENAME COLUMN tenant_id TO tenant_scope")
    op.execute(
        """
        ALTER TABLE window_aggregate
            ADD COLUMN window_size_minutes smallint NOT NULL DEFAULT 5,
            ADD COLUMN txn_type text NOT NULL DEFAULT '',
            ADD COLUMN instrument text NOT NULL DEFAULT '',
            ADD COLUMN channel_raw text NOT NULL DEFAULT '',
            ADD COLUMN operation_event_count integer NOT NULL DEFAULT 0,
            ADD COLUMN unknown_count integer NOT NULL DEFAULT 0,
            ADD COLUMN failed_rate numeric(7, 6),
            ADD COLUMN unknown_rate numeric(7, 6),
            ADD COLUMN monetary_count integer NOT NULL DEFAULT 0,
            ADD COLUMN monetary_amount_minor numeric(38, 0) NOT NULL DEFAULT 0,
            ADD COLUMN invalid_amount_count integer NOT NULL DEFAULT 0,
            ADD COLUMN missing_currency_count integer NOT NULL DEFAULT 0,
            ADD COLUMN source_event_count integer NOT NULL DEFAULT 0,
            ADD COLUMN last_event_at timestamptz,
            ADD COLUMN job_run_id uuid REFERENCES job_run_log(job_run_id)
        """
    )
    op.execute(
        """
        UPDATE window_aggregate
        SET operation_event_count = success_count + failed_count + pending_count,
            source_event_count = total_count,
            last_event_at = window_end,
            success_rate = CASE
                WHEN success_count + failed_count + pending_count = 0 THEN NULL
                ELSE round(
                    success_count::numeric /
                    (success_count + failed_count + pending_count), 6
                )
            END,
            failed_rate = CASE
                WHEN success_count + failed_count + pending_count = 0 THEN NULL
                ELSE round(
                    failed_count::numeric /
                    (success_count + failed_count + pending_count), 6
                )
            END,
            unknown_rate = CASE
                WHEN success_count + failed_count + pending_count = 0 THEN NULL
                ELSE 0
            END
        """
    )
    op.execute(
        """
        ALTER TABLE window_aggregate
            ALTER COLUMN last_event_at SET NOT NULL,
            DROP CONSTRAINT window_aggregate_pkey,
            ADD CONSTRAINT window_aggregate_pkey PRIMARY KEY (
                window_size_minutes, window_start, region, tenant_scope,
                provider, txn_type, instrument, channel_raw, currency
            ),
            ADD CONSTRAINT ck_window_aggregate_size
                CHECK (window_size_minutes IN (1, 5, 15)),
            ADD CONSTRAINT ck_window_aggregate_duration
                CHECK (
                    window_end = window_start
                        + make_interval(mins => window_size_minutes)
                ),
            ADD CONSTRAINT ck_window_aggregate_bucket_counts
                CHECK (
                    operation_event_count = success_count + failed_count
                        + pending_count + unknown_count
                ),
            ADD CONSTRAINT ck_window_aggregate_nonnegative_counts
                CHECK (
                    operation_event_count >= 0 AND unknown_count >= 0
                    AND monetary_count >= 0 AND invalid_amount_count >= 0
                    AND missing_currency_count >= 0 AND source_event_count >= 0
                ),
            ADD CONSTRAINT ck_window_aggregate_failed_rate
                CHECK (failed_rate IS NULL OR failed_rate BETWEEN 0 AND 1),
            ADD CONSTRAINT ck_window_aggregate_unknown_rate
                CHECK (unknown_rate IS NULL OR unknown_rate BETWEEN 0 AND 1)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_window_aggregate_scope_window
        ON window_aggregate (
            region, tenant_scope, window_size_minutes, window_start DESC
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_window_aggregate_scope_window")
    # Different sizes/dimensions cannot be represented by the old primary key.
    op.execute("TRUNCATE TABLE window_aggregate")
    op.execute(
        """
        ALTER TABLE window_aggregate
            DROP CONSTRAINT ck_window_aggregate_unknown_rate,
            DROP CONSTRAINT ck_window_aggregate_failed_rate,
            DROP CONSTRAINT ck_window_aggregate_nonnegative_counts,
            DROP CONSTRAINT ck_window_aggregate_bucket_counts,
            DROP CONSTRAINT ck_window_aggregate_duration,
            DROP CONSTRAINT ck_window_aggregate_size,
            DROP CONSTRAINT window_aggregate_pkey,
            ADD CONSTRAINT window_aggregate_pkey PRIMARY KEY (
                window_start, region, tenant_scope, provider, currency
            ),
            DROP COLUMN job_run_id,
            DROP COLUMN last_event_at,
            DROP COLUMN source_event_count,
            DROP COLUMN missing_currency_count,
            DROP COLUMN invalid_amount_count,
            DROP COLUMN monetary_amount_minor,
            DROP COLUMN monetary_count,
            DROP COLUMN unknown_rate,
            DROP COLUMN failed_rate,
            DROP COLUMN unknown_count,
            DROP COLUMN operation_event_count,
            DROP COLUMN channel_raw,
            DROP COLUMN instrument,
            DROP COLUMN txn_type,
            DROP COLUMN window_size_minutes
        """
    )
    op.execute("ALTER TABLE window_aggregate RENAME COLUMN tenant_scope TO tenant_id")

    op.execute("DROP INDEX IF EXISTS ix_canonical_event_transaction_history")
    op.execute(
        """
        ALTER TABLE canonical_event
            DROP CONSTRAINT uq_canonical_event_date_idempotency,
            DROP CONSTRAINT ck_canonical_event_event_date,
            DROP CONSTRAINT ck_canonical_event_idempotency_key,
            DROP CONSTRAINT ck_canonical_event_identity_kind,
            DROP COLUMN job_run_id,
            DROP COLUMN ingested_at,
            DROP COLUMN source_position,
            DROP COLUMN source_partition,
            DROP COLUMN source_name,
            DROP COLUMN tenant_scope,
            DROP COLUMN event_time,
            DROP COLUMN event_identity_kind,
            DROP COLUMN idempotency_key
        """
    )
    op.execute(
        """
        ALTER TABLE job_run_log
            DROP CONSTRAINT ck_job_run_log_data_range,
            DROP COLUMN data_to,
            DROP COLUMN data_from,
            DROP COLUMN source_checkpoint,
            DROP COLUMN source_name
        """
    )
