"""Normalize persisted values with built-in PostgreSQL types and checks."""

from alembic import op


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE ingest_message
            ALTER COLUMN event_type TYPE varchar(64);

        ALTER TABLE canonical_event
            ALTER COLUMN region TYPE varchar(5),
            ALTER COLUMN txn_type TYPE varchar(12),
            ALTER COLUMN provider TYPE varchar(64),
            ALTER COLUMN status TYPE varchar(16),
            ALTER COLUMN currency TYPE varchar(3);
        ALTER TABLE canonical_event
            ADD CONSTRAINT ck_canonical_event_region
                CHECK (region IN ('VN', 'ASIA', 'INDIA')),
            ADD CONSTRAINT ck_canonical_event_txn_type
                CHECK (txn_type IN (
                    'PAYMENT', 'LOAD', 'REFUND', 'CARD_LINK',
                    'TOKENIZATION', 'UNKNOWN'
                )),
            ADD CONSTRAINT ck_canonical_event_status
                CHECK (status IN (
                    'CREATED', 'PENDING', 'SUCCESS', 'FAILED', 'REFUNDED',
                    'PARTIAL_REFUNDED', 'EXPIRED', 'CANCELLED', 'UNKNOWN'
                )),
            ADD CONSTRAINT ck_canonical_event_currency
                CHECK (currency IS NULL OR currency IN ('VND', 'IDR', 'INR', 'PHP', 'USD'));

        ALTER TABLE job_run_log ALTER COLUMN status DROP DEFAULT;
        ALTER TABLE job_run_log
            ALTER COLUMN job_name TYPE varchar(64),
            ALTER COLUMN region TYPE varchar(5),
            ALTER COLUMN provider TYPE varchar(64),
            ALTER COLUMN status TYPE varchar(9),
            ALTER COLUMN error_code TYPE varchar(64);
        ALTER TABLE job_run_log ALTER COLUMN status SET DEFAULT 'RUNNING';
        ALTER TABLE job_run_log
            ADD CONSTRAINT ck_job_run_log_region
                CHECK (region IS NULL OR region IN ('VN', 'ASIA', 'INDIA')),
            ADD CONSTRAINT ck_job_run_log_status
                CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED'));

        ALTER TABLE recon_run ALTER COLUMN status DROP DEFAULT;
        ALTER TABLE recon_run
            ALTER COLUMN region TYPE varchar(5),
            ALTER COLUMN provider TYPE varchar(64),
            ALTER COLUMN file_checksum TYPE varchar(64),
            ALTER COLUMN business_timezone TYPE varchar(64),
            ALTER COLUMN status TYPE varchar(9);
        ALTER TABLE recon_run ALTER COLUMN status SET DEFAULT 'PENDING';
        ALTER TABLE recon_run
            ADD CONSTRAINT ck_recon_run_region
                CHECK (region IN ('VN', 'ASIA', 'INDIA')),
            ADD CONSTRAINT ck_recon_run_file_checksum
                CHECK (file_checksum ~ '^[0-9a-f]{64}$'),
            ADD CONSTRAINT ck_recon_run_status
                CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED'));

        ALTER TABLE recon_result
            ALTER COLUMN result_key TYPE integer USING result_key::integer,
            ALTER COLUMN result_type TYPE varchar(19),
            ALTER COLUMN region TYPE varchar(5),
            ALTER COLUMN provider TYPE varchar(64),
            ALTER COLUMN txn_type TYPE varchar(12),
            ALTER COLUMN system_currency TYPE varchar(3),
            ALTER COLUMN provider_currency TYPE varchar(3),
            ALTER COLUMN system_status TYPE varchar(16);
        ALTER TABLE recon_result
            ADD CONSTRAINT ck_recon_result_key_positive CHECK (result_key > 0),
            ADD CONSTRAINT ck_recon_result_region
                CHECK (region IN ('VN', 'ASIA', 'INDIA')),
            ADD CONSTRAINT ck_recon_result_txn_type
                CHECK (txn_type IS NULL OR txn_type IN (
                    'PAYMENT', 'LOAD', 'REFUND', 'CARD_LINK',
                    'TOKENIZATION', 'UNKNOWN'
                )),
            ADD CONSTRAINT ck_recon_result_system_currency
                CHECK (system_currency IS NULL OR system_currency IN ('VND', 'IDR', 'INR', 'PHP', 'USD')),
            ADD CONSTRAINT ck_recon_result_provider_currency
                CHECK (provider_currency IS NULL OR provider_currency IN ('VND', 'IDR', 'INR', 'PHP', 'USD')),
            ADD CONSTRAINT ck_recon_result_system_status
                CHECK (system_status IS NULL OR system_status IN (
                    'CREATED', 'PENDING', 'SUCCESS', 'FAILED', 'REFUNDED',
                    'PARTIAL_REFUNDED', 'EXPIRED', 'CANCELLED', 'UNKNOWN'
                ));

        ALTER TABLE provider_txn
            ALTER COLUMN region TYPE varchar(5),
            ALTER COLUMN provider TYPE varchar(64),
            ALTER COLUMN txn_type TYPE varchar(12),
            ALTER COLUMN currency TYPE varchar(3),
            ALTER COLUMN status TYPE varchar(64);
        ALTER TABLE provider_txn
            ADD CONSTRAINT ck_provider_txn_region
                CHECK (region IN ('VN', 'ASIA', 'INDIA')),
            ADD CONSTRAINT ck_provider_txn_type
                CHECK (txn_type IN (
                    'PAYMENT', 'LOAD', 'REFUND', 'CARD_LINK',
                    'TOKENIZATION', 'UNKNOWN'
                )),
            ADD CONSTRAINT ck_provider_txn_currency
                CHECK (currency IN ('VND', 'IDR', 'INR', 'PHP', 'USD'));

        -- Snapshot the run's business day into both detail tables. PostgreSQL
        -- requires the partition key in parent-level PK/UNIQUE constraints.
        ALTER TABLE recon_run
            ADD CONSTRAINT uq_recon_run_identity_date
                UNIQUE (run_id, job_run_id, business_date);

        ALTER TABLE recon_result ADD COLUMN business_date date;
        UPDATE recon_result AS result
        SET business_date = run.business_date
        FROM recon_run AS run
        WHERE run.run_id = result.run_id
          AND run.job_run_id = result.job_run_id;
        ALTER TABLE recon_result ALTER COLUMN business_date SET NOT NULL;

        ALTER TABLE provider_txn ADD COLUMN business_date date;
        UPDATE provider_txn AS txn
        SET business_date = run.business_date
        FROM recon_run AS run
        WHERE run.run_id = txn.run_id
          AND run.job_run_id = txn.job_run_id;
        ALTER TABLE provider_txn ALTER COLUMN business_date SET NOT NULL;

        ALTER TABLE recon_result RENAME TO recon_result_unpartitioned;
        ALTER TABLE provider_txn RENAME TO provider_txn_unpartitioned;

        CREATE TABLE recon_result (
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
            created_at timestamptz NOT NULL DEFAULT now(),
            system_provider_txn_id text,
            system_status varchar(16),
            refund_of text,
            refunded_amount_minor numeric(38, 0),
            CONSTRAINT pk_recon_result_partitioned
                PRIMARY KEY (business_date, result_id),
            CONSTRAINT uq_recon_result_partitioned_key
                UNIQUE (business_date, run_id, result_key),
            CONSTRAINT fk_recon_result_run
                FOREIGN KEY (run_id, job_run_id, business_date)
                REFERENCES recon_run(run_id, job_run_id, business_date)
                ON DELETE CASCADE,
            CONSTRAINT ck_recon_result_key_positive CHECK (result_key > 0),
            CONSTRAINT ck_recon_result_type CHECK (result_type IN (
                'MATCHED', 'AMOUNT_MISMATCH', 'CURRENCY_MISMATCH',
                'MISSING_IN_SYSTEM', 'MISSING_IN_PROVIDER', 'DUPLICATE',
                'FUZZY_MATCHED', 'NOT_RECONCILABLE', 'INVALID_DATA'
            )),
            CONSTRAINT ck_recon_result_region
                CHECK (region IN ('VN', 'ASIA', 'INDIA')),
            CONSTRAINT ck_recon_result_txn_type CHECK (
                txn_type IS NULL OR txn_type IN (
                    'PAYMENT', 'LOAD', 'REFUND', 'CARD_LINK',
                    'TOKENIZATION', 'UNKNOWN'
                )
            ),
            CONSTRAINT ck_recon_result_system_currency CHECK (
                system_currency IS NULL OR system_currency IN ('VND', 'IDR', 'INR', 'PHP', 'USD')
            ),
            CONSTRAINT ck_recon_result_provider_currency CHECK (
                provider_currency IS NULL OR provider_currency IN ('VND', 'IDR', 'INR', 'PHP', 'USD')
            ),
            CONSTRAINT ck_recon_result_system_status CHECK (
                system_status IS NULL OR system_status IN (
                    'CREATED', 'PENDING', 'SUCCESS', 'FAILED', 'REFUNDED',
                    'PARTIAL_REFUNDED', 'EXPIRED', 'CANCELLED', 'UNKNOWN'
                )
            ),
            CONSTRAINT ck_recon_result_source_row
                CHECK (source_row_number IS NULL OR source_row_number > 0)
        ) PARTITION BY RANGE (business_date);

        CREATE TABLE provider_txn (
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
            late_arriving boolean NOT NULL DEFAULT false,
            source_file text NOT NULL,
            source_row_number integer NOT NULL,
            raw jsonb NOT NULL DEFAULT '{}'::jsonb,
            ingested_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_provider_txn_partitioned
                PRIMARY KEY (business_date, provider_row_id),
            CONSTRAINT uq_provider_txn_partitioned_source_row
                UNIQUE (business_date, run_id, source_file, source_row_number),
            CONSTRAINT fk_provider_txn_run
                FOREIGN KEY (run_id, job_run_id, business_date)
                REFERENCES recon_run(run_id, job_run_id, business_date)
                ON DELETE CASCADE,
            CONSTRAINT ck_provider_txn_source_row CHECK (source_row_number > 0),
            CONSTRAINT ck_provider_txn_amount CHECK (amount_minor >= 0),
            CONSTRAINT ck_provider_txn_region
                CHECK (region IN ('VN', 'ASIA', 'INDIA')),
            CONSTRAINT ck_provider_txn_type CHECK (txn_type IN (
                'PAYMENT', 'LOAD', 'REFUND', 'CARD_LINK',
                'TOKENIZATION', 'UNKNOWN'
            )),
            CONSTRAINT ck_provider_txn_currency
                CHECK (currency IN ('VND', 'IDR', 'INR', 'PHP', 'USD'))
        ) PARTITION BY RANGE (business_date);

        -- Serialize first-use DDL per day so concurrent runs for the same date
        -- cannot race while provisioning their detail partitions.
        CREATE FUNCTION ensure_reconciliation_day_partitions(partition_day date)
        RETURNS void
        LANGUAGE plpgsql
        AS $partition_function$
        DECLARE
            suffix text;
        BEGIN
            IF partition_day IS NULL THEN
                RAISE EXCEPTION 'partition day is required';
            END IF;
            PERFORM pg_advisory_xact_lock(
                hashtextextended('reconciliation-partition:' || partition_day::text, 0)
            );
            suffix := to_char(partition_day, 'YYYYMMDD');
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF recon_result '
                'FOR VALUES FROM (%L) TO (%L)',
                'recon_result_' || suffix, partition_day, partition_day + 1
            );
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF provider_txn '
                'FOR VALUES FROM (%L) TO (%L)',
                'provider_txn_' || suffix, partition_day, partition_day + 1
            );
        END
        $partition_function$;

        SELECT ensure_reconciliation_day_partitions(business_date)
        FROM (
            SELECT DISTINCT business_date FROM recon_result_unpartitioned
            UNION
            SELECT DISTINCT business_date FROM provider_txn_unpartitioned
        ) AS existing_days;

        INSERT INTO recon_result (
            result_id, business_date, run_id, job_run_id, result_key,
            result_type, region, tenant_id, provider, txn_type, system_txn_id,
            system_amount_minor, system_currency, provider_txn_id,
            provider_amount_minor, provider_currency, delta_amount, source_file,
            source_row_number, reason, created_at, system_provider_txn_id,
            system_status, refund_of, refunded_amount_minor
        ) OVERRIDING SYSTEM VALUE
        SELECT result_id, business_date, run_id, job_run_id, result_key,
               result_type, region, tenant_id, provider, txn_type, system_txn_id,
               system_amount_minor, system_currency, provider_txn_id,
               provider_amount_minor, provider_currency, delta_amount, source_file,
               source_row_number, reason, created_at, system_provider_txn_id,
               system_status, refund_of, refunded_amount_minor
        FROM recon_result_unpartitioned;

        INSERT INTO provider_txn (
            provider_row_id, business_date, run_id, job_run_id, region,
            tenant_id, provider, txn_type, provider_txn_id, provider_ref_id,
            merchant_ref, merchant_id, amount_minor, currency, status,
            transaction_time, late_arriving, source_file, source_row_number,
            raw, ingested_at
        ) OVERRIDING SYSTEM VALUE
        SELECT provider_row_id, business_date, run_id, job_run_id, region,
               tenant_id, provider, txn_type, provider_txn_id, provider_ref_id,
               merchant_ref, merchant_id, amount_minor, currency, status,
               transaction_time, late_arriving, source_file, source_row_number,
               raw, ingested_at
        FROM provider_txn_unpartitioned;

        SELECT setval(
            pg_get_serial_sequence('recon_result', 'result_id'),
            COALESCE((SELECT max(result_id) FROM recon_result), 1),
            EXISTS (SELECT 1 FROM recon_result)
        );
        SELECT setval(
            pg_get_serial_sequence('provider_txn', 'provider_row_id'),
            COALESCE((SELECT max(provider_row_id) FROM provider_txn), 1),
            EXISTS (SELECT 1 FROM provider_txn)
        );

        DROP TABLE recon_result_unpartitioned;
        DROP TABLE provider_txn_unpartitioned;

        ALTER TABLE recon_result
            RENAME CONSTRAINT pk_recon_result_partitioned TO recon_result_pkey;
        ALTER TABLE recon_result
            RENAME CONSTRAINT uq_recon_result_partitioned_key TO uq_recon_result_key;
        ALTER TABLE provider_txn
            RENAME CONSTRAINT pk_provider_txn_partitioned TO provider_txn_pkey;
        ALTER TABLE provider_txn
            RENAME CONSTRAINT uq_provider_txn_partitioned_source_row
            TO uq_provider_txn_source_row;

        CREATE INDEX ix_recon_result_run_type
            ON recon_result (run_id, result_type, result_key);
        CREATE INDEX ix_recon_result_attention
            ON recon_result (run_id, region, tenant_id, provider, result_type)
            WHERE result_type NOT IN ('MATCHED', 'FUZZY_MATCHED');
        CREATE INDEX ix_provider_txn_run_source
            ON provider_txn (run_id, source_file, source_row_number);
        CREATE INDEX ix_provider_txn_scope_time
            ON provider_txn (
                region, tenant_id, provider, currency, transaction_time
            );
        CREATE INDEX ix_provider_txn_provider_id
            ON provider_txn (
                region, tenant_id, provider, currency, provider_txn_id
            ) WHERE provider_txn_id IS NOT NULL;
        CREATE INDEX ix_provider_txn_transaction_time_brin
            ON provider_txn USING brin (transaction_time);
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP FUNCTION ensure_reconciliation_day_partitions(date);

        ALTER TABLE recon_result RENAME TO recon_result_partitioned;
        ALTER TABLE provider_txn RENAME TO provider_txn_partitioned;

        CREATE TABLE recon_result (
            result_id bigint GENERATED ALWAYS AS IDENTITY,
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
            created_at timestamptz NOT NULL DEFAULT now(),
            system_provider_txn_id text,
            system_status varchar(16),
            refund_of text,
            refunded_amount_minor numeric(38, 0),
            CONSTRAINT pk_recon_result_regular PRIMARY KEY (result_id),
            CONSTRAINT uq_recon_result_regular_key UNIQUE (run_id, result_key),
            CONSTRAINT fk_recon_result_run
                FOREIGN KEY (run_id, job_run_id)
                REFERENCES recon_run(run_id, job_run_id) ON DELETE CASCADE,
            CONSTRAINT ck_recon_result_key_positive CHECK (result_key > 0),
            CONSTRAINT ck_recon_result_type CHECK (result_type IN (
                'MATCHED', 'AMOUNT_MISMATCH', 'CURRENCY_MISMATCH',
                'MISSING_IN_SYSTEM', 'MISSING_IN_PROVIDER', 'DUPLICATE',
                'FUZZY_MATCHED', 'NOT_RECONCILABLE', 'INVALID_DATA'
            )),
            CONSTRAINT ck_recon_result_region
                CHECK (region IN ('VN', 'ASIA', 'INDIA')),
            CONSTRAINT ck_recon_result_txn_type CHECK (
                txn_type IS NULL OR txn_type IN (
                    'PAYMENT', 'LOAD', 'REFUND', 'CARD_LINK',
                    'TOKENIZATION', 'UNKNOWN'
                )
            ),
            CONSTRAINT ck_recon_result_system_currency CHECK (
                system_currency IS NULL OR system_currency IN ('VND', 'IDR', 'INR', 'PHP', 'USD')
            ),
            CONSTRAINT ck_recon_result_provider_currency CHECK (
                provider_currency IS NULL OR provider_currency IN ('VND', 'IDR', 'INR', 'PHP', 'USD')
            ),
            CONSTRAINT ck_recon_result_system_status CHECK (
                system_status IS NULL OR system_status IN (
                    'CREATED', 'PENDING', 'SUCCESS', 'FAILED', 'REFUNDED',
                    'PARTIAL_REFUNDED', 'EXPIRED', 'CANCELLED', 'UNKNOWN'
                )
            ),
            CONSTRAINT ck_recon_result_source_row
                CHECK (source_row_number IS NULL OR source_row_number > 0)
        );

        CREATE TABLE provider_txn (
            provider_row_id bigint GENERATED ALWAYS AS IDENTITY,
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
            late_arriving boolean NOT NULL DEFAULT false,
            source_file text NOT NULL,
            source_row_number integer NOT NULL,
            raw jsonb NOT NULL DEFAULT '{}'::jsonb,
            ingested_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_provider_txn_regular PRIMARY KEY (provider_row_id),
            CONSTRAINT uq_provider_txn_regular_source_row
                UNIQUE (run_id, source_file, source_row_number),
            CONSTRAINT fk_provider_txn_run
                FOREIGN KEY (run_id, job_run_id)
                REFERENCES recon_run(run_id, job_run_id) ON DELETE CASCADE,
            CONSTRAINT ck_provider_txn_source_row CHECK (source_row_number > 0),
            CONSTRAINT ck_provider_txn_amount CHECK (amount_minor >= 0),
            CONSTRAINT ck_provider_txn_region
                CHECK (region IN ('VN', 'ASIA', 'INDIA')),
            CONSTRAINT ck_provider_txn_type CHECK (txn_type IN (
                'PAYMENT', 'LOAD', 'REFUND', 'CARD_LINK',
                'TOKENIZATION', 'UNKNOWN'
            )),
            CONSTRAINT ck_provider_txn_currency
                CHECK (currency IN ('VND', 'IDR', 'INR', 'PHP', 'USD'))
        );

        INSERT INTO recon_result (
            result_id, run_id, job_run_id, result_key, result_type, region,
            tenant_id, provider, txn_type, system_txn_id, system_amount_minor,
            system_currency, provider_txn_id, provider_amount_minor,
            provider_currency, delta_amount, source_file, source_row_number,
            reason, created_at, system_provider_txn_id, system_status,
            refund_of, refunded_amount_minor
        ) OVERRIDING SYSTEM VALUE
        SELECT result_id, run_id, job_run_id, result_key, result_type, region,
               tenant_id, provider, txn_type, system_txn_id, system_amount_minor,
               system_currency, provider_txn_id, provider_amount_minor,
               provider_currency, delta_amount, source_file, source_row_number,
               reason, created_at, system_provider_txn_id, system_status,
               refund_of, refunded_amount_minor
        FROM recon_result_partitioned;

        INSERT INTO provider_txn (
            provider_row_id, run_id, job_run_id, region, tenant_id, provider,
            txn_type, provider_txn_id, provider_ref_id, merchant_ref,
            merchant_id, amount_minor, currency, status, transaction_time,
            late_arriving, source_file, source_row_number, raw, ingested_at
        ) OVERRIDING SYSTEM VALUE
        SELECT provider_row_id, run_id, job_run_id, region, tenant_id, provider,
               txn_type, provider_txn_id, provider_ref_id, merchant_ref,
               merchant_id, amount_minor, currency, status, transaction_time,
               late_arriving, source_file, source_row_number, raw, ingested_at
        FROM provider_txn_partitioned;

        SELECT setval(
            pg_get_serial_sequence('recon_result', 'result_id'),
            COALESCE((SELECT max(result_id) FROM recon_result), 1),
            EXISTS (SELECT 1 FROM recon_result)
        );
        SELECT setval(
            pg_get_serial_sequence('provider_txn', 'provider_row_id'),
            COALESCE((SELECT max(provider_row_id) FROM provider_txn), 1),
            EXISTS (SELECT 1 FROM provider_txn)
        );

        DROP TABLE recon_result_partitioned CASCADE;
        DROP TABLE provider_txn_partitioned CASCADE;

        ALTER TABLE recon_result
            RENAME CONSTRAINT pk_recon_result_regular TO recon_result_pkey;
        ALTER TABLE recon_result
            RENAME CONSTRAINT uq_recon_result_regular_key TO uq_recon_result_key;
        ALTER TABLE provider_txn
            RENAME CONSTRAINT pk_provider_txn_regular TO provider_txn_pkey;
        ALTER TABLE provider_txn
            RENAME CONSTRAINT uq_provider_txn_regular_source_row
            TO uq_provider_txn_source_row;

        CREATE INDEX ix_recon_result_run_type
            ON recon_result (run_id, result_type);
        CREATE INDEX ix_recon_result_attention
            ON recon_result (run_id, region, tenant_id, provider, result_type)
            WHERE result_type NOT IN ('MATCHED', 'FUZZY_MATCHED');
        CREATE INDEX ix_provider_txn_scope_time
            ON provider_txn (
                region, tenant_id, provider, currency, transaction_time
            );
        CREATE INDEX ix_provider_txn_provider_id
            ON provider_txn (
                region, tenant_id, provider, currency, provider_txn_id
            ) WHERE provider_txn_id IS NOT NULL;
        CREATE INDEX ix_provider_txn_transaction_time_brin
            ON provider_txn USING brin (transaction_time);

        ALTER TABLE recon_run
            DROP CONSTRAINT uq_recon_run_identity_date;

        ALTER TABLE provider_txn
            DROP CONSTRAINT ck_provider_txn_currency,
            DROP CONSTRAINT ck_provider_txn_type,
            DROP CONSTRAINT ck_provider_txn_region;
        ALTER TABLE provider_txn
            ALTER COLUMN region TYPE text,
            ALTER COLUMN provider TYPE text,
            ALTER COLUMN txn_type TYPE text,
            ALTER COLUMN currency TYPE text,
            ALTER COLUMN status TYPE text;

        ALTER TABLE recon_result
            DROP CONSTRAINT ck_recon_result_system_status,
            DROP CONSTRAINT ck_recon_result_provider_currency,
            DROP CONSTRAINT ck_recon_result_system_currency,
            DROP CONSTRAINT ck_recon_result_txn_type,
            DROP CONSTRAINT ck_recon_result_region,
            DROP CONSTRAINT ck_recon_result_key_positive;
        ALTER TABLE recon_result
            ALTER COLUMN result_key TYPE text USING result_key::text,
            ALTER COLUMN result_type TYPE text,
            ALTER COLUMN region TYPE text,
            ALTER COLUMN provider TYPE text,
            ALTER COLUMN txn_type TYPE text,
            ALTER COLUMN system_currency TYPE text,
            ALTER COLUMN provider_currency TYPE text,
            ALTER COLUMN system_status TYPE text;

        ALTER TABLE recon_run
            DROP CONSTRAINT ck_recon_run_status,
            DROP CONSTRAINT ck_recon_run_file_checksum,
            DROP CONSTRAINT ck_recon_run_region;
        ALTER TABLE recon_run ALTER COLUMN status DROP DEFAULT;
        ALTER TABLE recon_run
            ALTER COLUMN region TYPE text,
            ALTER COLUMN provider TYPE text,
            ALTER COLUMN file_checksum TYPE text,
            ALTER COLUMN business_timezone TYPE text,
            ALTER COLUMN status TYPE text;
        ALTER TABLE recon_run ALTER COLUMN status SET DEFAULT 'PENDING';

        ALTER TABLE job_run_log
            DROP CONSTRAINT ck_job_run_log_status,
            DROP CONSTRAINT ck_job_run_log_region;
        ALTER TABLE job_run_log ALTER COLUMN status DROP DEFAULT;
        ALTER TABLE job_run_log
            ALTER COLUMN job_name TYPE text,
            ALTER COLUMN region TYPE text,
            ALTER COLUMN provider TYPE text,
            ALTER COLUMN status TYPE text,
            ALTER COLUMN error_code TYPE text;
        ALTER TABLE job_run_log ALTER COLUMN status SET DEFAULT 'RUNNING';

        ALTER TABLE canonical_event
            DROP CONSTRAINT ck_canonical_event_currency,
            DROP CONSTRAINT ck_canonical_event_status,
            DROP CONSTRAINT ck_canonical_event_txn_type,
            DROP CONSTRAINT ck_canonical_event_region;
        ALTER TABLE canonical_event
            ALTER COLUMN region TYPE text,
            ALTER COLUMN txn_type TYPE text,
            ALTER COLUMN provider TYPE text,
            ALTER COLUMN status TYPE text,
            ALTER COLUMN currency TYPE text;

        ALTER TABLE ingest_message
            ALTER COLUMN event_type TYPE text;
        """
    )
