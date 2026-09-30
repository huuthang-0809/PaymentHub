-- Isolated schema for comparing the current and proposed storage layouts.
CREATE SCHEMA bench_week3;
SET LOCAL search_path TO bench_week3;

CREATE TABLE recon_run (
    run_id uuid PRIMARY KEY,
    job_run_id uuid NOT NULL,
    business_date date NOT NULL,
    region varchar(5) NOT NULL,
    tenant_id text,
    provider varchar(64) NOT NULL,
    source_file text NOT NULL,
    file_checksum varchar(64) NOT NULL,
    status varchar(9) NOT NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    UNIQUE (run_id, job_run_id, business_date)
);

-- BEFORE: equivalent to the regular detail tables used by the application.
-- business_date is present here only so before/after return identical columns.
CREATE TABLE recon_result_before (
    result_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    business_date date NOT NULL,
    run_id uuid NOT NULL,
    job_run_id uuid NOT NULL,
    result_key integer NOT NULL,
    result_type varchar(19) NOT NULL,
    region varchar(5) NOT NULL,
    tenant_id text,
    provider varchar(64) NOT NULL,
    txn_type varchar(12),
    system_txn_id text,
    system_amount_minor numeric(38, 0),
    system_currency varchar(3),
    provider_txn_id text,
    provider_amount_minor numeric(38, 0),
    provider_currency varchar(3),
    delta_amount numeric(38, 0),
    source_file text,
    source_row_number integer,
    reason text,
    created_at timestamptz NOT NULL,
    FOREIGN KEY (run_id, job_run_id, business_date)
        REFERENCES recon_run(run_id, job_run_id, business_date),
    UNIQUE (run_id, result_key)
);
CREATE INDEX ix_result_before_run_type
    ON recon_result_before (run_id, result_type);
CREATE INDEX ix_result_before_attention
    ON recon_result_before (run_id, region, tenant_id, provider, result_type)
    WHERE result_type NOT IN ('MATCHED', 'FUZZY_MATCHED');

CREATE TABLE provider_txn_before (
    provider_row_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    business_date date NOT NULL,
    run_id uuid NOT NULL,
    job_run_id uuid NOT NULL,
    region varchar(5) NOT NULL,
    tenant_id text,
    provider varchar(64) NOT NULL,
    txn_type varchar(12) NOT NULL,
    provider_txn_id text,
    provider_ref_id text,
    merchant_ref text,
    merchant_id text,
    amount_minor numeric(38, 0) NOT NULL,
    currency varchar(3) NOT NULL,
    status varchar(64) NOT NULL,
    transaction_time timestamptz NOT NULL,
    source_file text NOT NULL,
    source_row_number integer NOT NULL,
    raw jsonb NOT NULL,
    ingested_at timestamptz NOT NULL,
    FOREIGN KEY (run_id, job_run_id, business_date)
        REFERENCES recon_run(run_id, job_run_id, business_date),
    UNIQUE (run_id, source_file, source_row_number)
);
CREATE INDEX ix_provider_before_scope_time
    ON provider_txn_before
       (region, tenant_id, provider, currency, transaction_time);
CREATE INDEX ix_provider_before_provider_id
    ON provider_txn_before
       (region, tenant_id, provider, currency, provider_txn_id)
    WHERE provider_txn_id IS NOT NULL;
CREATE INDEX ix_provider_before_time_brin
    ON provider_txn_before USING brin (transaction_time);

-- AFTER: proposed daily partitions. PostgreSQL requires partition keys in
-- parent-level primary/unique constraints.
CREATE TABLE recon_result_after (
    result_id bigint GENERATED ALWAYS AS IDENTITY,
    business_date date NOT NULL,
    run_id uuid NOT NULL,
    job_run_id uuid NOT NULL,
    result_key integer NOT NULL,
    result_type varchar(19) NOT NULL,
    region varchar(5) NOT NULL,
    tenant_id text,
    provider varchar(64) NOT NULL,
    txn_type varchar(12),
    system_txn_id text,
    system_amount_minor numeric(38, 0),
    system_currency varchar(3),
    provider_txn_id text,
    provider_amount_minor numeric(38, 0),
    provider_currency varchar(3),
    delta_amount numeric(38, 0),
    source_file text,
    source_row_number integer,
    reason text,
    created_at timestamptz NOT NULL,
    PRIMARY KEY (business_date, result_id),
    FOREIGN KEY (run_id, job_run_id, business_date)
        REFERENCES recon_run(run_id, job_run_id, business_date),
    UNIQUE (business_date, run_id, result_key)
) PARTITION BY RANGE (business_date);
CREATE INDEX ix_result_after_run_type_key
    ON recon_result_after (run_id, result_type, result_key);
CREATE INDEX ix_result_after_attention
    ON recon_result_after (run_id, region, tenant_id, provider, result_type)
    WHERE result_type NOT IN ('MATCHED', 'FUZZY_MATCHED');

CREATE TABLE provider_txn_after (
    provider_row_id bigint GENERATED ALWAYS AS IDENTITY,
    business_date date NOT NULL,
    run_id uuid NOT NULL,
    job_run_id uuid NOT NULL,
    region varchar(5) NOT NULL,
    tenant_id text,
    provider varchar(64) NOT NULL,
    txn_type varchar(12) NOT NULL,
    provider_txn_id text,
    provider_ref_id text,
    merchant_ref text,
    merchant_id text,
    amount_minor numeric(38, 0) NOT NULL,
    currency varchar(3) NOT NULL,
    status varchar(64) NOT NULL,
    transaction_time timestamptz NOT NULL,
    source_file text NOT NULL,
    source_row_number integer NOT NULL,
    raw jsonb NOT NULL,
    ingested_at timestamptz NOT NULL,
    PRIMARY KEY (business_date, provider_row_id),
    FOREIGN KEY (run_id, job_run_id, business_date)
        REFERENCES recon_run(run_id, job_run_id, business_date),
    UNIQUE (business_date, run_id, source_file, source_row_number)
) PARTITION BY RANGE (business_date);
CREATE INDEX ix_provider_after_run_source
    ON provider_txn_after (run_id, source_file, source_row_number);
CREATE INDEX ix_provider_after_scope_time
    ON provider_txn_after
       (region, tenant_id, provider, currency, transaction_time);
CREATE INDEX ix_provider_after_provider_id
    ON provider_txn_after
       (region, tenant_id, provider, currency, provider_txn_id)
    WHERE provider_txn_id IS NOT NULL;
CREATE INDEX ix_provider_after_time_brin
    ON provider_txn_after USING brin (transaction_time);

DO $$
DECLARE
    partition_day date;
    suffix text;
BEGIN
    FOR day_offset IN 0..29 LOOP
        partition_day := DATE '2026-01-01' + day_offset;
        suffix := to_char(partition_day, 'YYYYMMDD');
        EXECUTE format(
            'CREATE TABLE recon_result_after_%s PARTITION OF recon_result_after '
            'FOR VALUES FROM (%L) TO (%L)',
            suffix, partition_day, partition_day + 1
        );
        EXECUTE format(
            'CREATE TABLE provider_txn_after_%s PARTITION OF provider_txn_after '
            'FOR VALUES FROM (%L) TO (%L)',
            suffix, partition_day, partition_day + 1
        );
    END LOOP;
END $$;

