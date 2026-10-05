-- DDL extracted verbatim from upgrade() in migrations 0001–0006.
-- Dedicated schema; CREATE fails if it already exists. Always run in a transaction.
CREATE SCHEMA bench_week2;
SET LOCAL search_path TO bench_week2;

-- 0001_canonical_event.py
CREATE TABLE ingest_message (
            event_id uuid PRIMARY KEY,
            event_type text NOT NULL,
            payload_hash text NOT NULL,
            accepted_at timestamptz NOT NULL DEFAULT now()
        );

-- 0001_canonical_event.py
CREATE TABLE canonical_event (
            event_day date NOT NULL,
            event_id uuid NOT NULL REFERENCES ingest_message(event_id),
            region text NOT NULL,
            tenant_id text,
            txn_id text NOT NULL,
            txn_type text NOT NULL,
            provider text NOT NULL,
            status text NOT NULL,
            provider_txn_id text,
            provider_ref_id text,
            merchant_ref text,
            refund_of text,
            amount_minor numeric(38, 0),
            currency text,
            updated_at timestamptz NOT NULL,
            is_late boolean NOT NULL DEFAULT false,
            PRIMARY KEY (event_day, event_id)
        ) PARTITION BY RANGE (event_day);

-- 0001_canonical_event.py
CREATE INDEX ix_canonical_scope_time ON canonical_event (region, tenant_id, provider, updated_at DESC);

-- 0001_canonical_event.py
CREATE INDEX ix_canonical_updated_brin ON canonical_event USING brin (updated_at);

-- 0002_job_run_log.py
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
        );

-- 0002_job_run_log.py
CREATE INDEX ix_job_run_log_scope_date
        ON job_run_log (job_name, business_date, region, tenant_id, provider);

-- 0003_recon_run.py
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
        );

-- 0003_recon_run.py
CREATE INDEX ix_recon_run_scope_date
        ON recon_run (business_date, region, tenant_id, provider);

-- 0004_recon_result.py
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
        );

-- 0004_recon_result.py
CREATE INDEX ix_recon_result_run_type
        ON recon_result (run_id, result_type);

-- 0004_recon_result.py
CREATE INDEX ix_recon_result_attention
        ON recon_result (run_id, region, tenant_id, provider, result_type)
        WHERE result_type NOT IN ('MATCHED', 'FUZZY_MATCHED');

-- 0005_provider_txn.py
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
        );

-- 0005_provider_txn.py
CREATE INDEX ix_provider_txn_scope_time
        ON provider_txn (
            region, tenant_id, provider, currency, transaction_time
        );

-- 0005_provider_txn.py
CREATE INDEX ix_provider_txn_provider_id
        ON provider_txn (region, tenant_id, provider, currency, provider_txn_id)
        WHERE provider_txn_id IS NOT NULL;

-- 0005_provider_txn.py
CREATE INDEX ix_provider_txn_transaction_time_brin
        ON provider_txn USING brin (transaction_time);

-- 0006_recon_result_refund_evidence.py
ALTER TABLE recon_result
            ADD COLUMN system_provider_txn_id text,
            ADD COLUMN system_status text,
            ADD COLUMN refund_of text,
            ADD COLUMN refunded_amount_minor numeric(38, 0);