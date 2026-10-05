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

        ALTER TABLE window_aggregate ALTER COLUMN currency DROP DEFAULT;
        ALTER TABLE window_aggregate
            ALTER COLUMN region TYPE varchar(5),
            ALTER COLUMN tenant_id TYPE text,
            ALTER COLUMN provider TYPE varchar(64),
            ALTER COLUMN currency TYPE varchar(3) USING rtrim(currency)::varchar(3);
        ALTER TABLE window_aggregate ALTER COLUMN currency SET DEFAULT '';
        ALTER TABLE window_aggregate
            ADD CONSTRAINT ck_window_aggregate_region
                CHECK (region IN ('VN', 'ASIA', 'INDIA')),
            ADD CONSTRAINT ck_window_aggregate_currency
                CHECK (currency = '' OR currency IN ('VND', 'IDR', 'INR', 'PHP', 'USD'));
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE window_aggregate
            DROP CONSTRAINT ck_window_aggregate_currency,
            DROP CONSTRAINT ck_window_aggregate_region;
        ALTER TABLE window_aggregate ALTER COLUMN currency DROP DEFAULT;
        ALTER TABLE window_aggregate
            ALTER COLUMN region TYPE varchar(16),
            ALTER COLUMN tenant_id TYPE varchar(100),
            ALTER COLUMN provider TYPE varchar(100),
            ALTER COLUMN currency TYPE char(3);
        ALTER TABLE window_aggregate ALTER COLUMN currency SET DEFAULT '';

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
