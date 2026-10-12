"""Add durable queue and require monitoring result lineage.

Revision ID: 0018
Revises: 0017
"""

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO job_run_log (
            job_run_id, job_name, business_date, status, source_name,
            started_at, finished_at, metrics
        ) VALUES (
            '00000000-0000-0000-0000-000000000001',
            'legacy-lineage-backfill', CURRENT_DATE, 'COMPLETED', 'migration-0018',
            '1970-01-01T00:00:00Z', '1970-01-01T00:00:00Z', '{"backfilled": true}'::jsonb
        ) ON CONFLICT (job_run_id) DO NOTHING;

        UPDATE canonical_event
        SET job_run_id = '00000000-0000-0000-0000-000000000001'
        WHERE job_run_id IS NULL;
        UPDATE window_aggregate
        SET job_run_id = '00000000-0000-0000-0000-000000000001'
        WHERE job_run_id IS NULL;
        UPDATE late_event_audit
        SET job_run_id = '00000000-0000-0000-0000-000000000001'
        WHERE job_run_id IS NULL;

        ALTER TABLE canonical_event ALTER COLUMN job_run_id SET NOT NULL;
        ALTER TABLE window_aggregate ALTER COLUMN job_run_id SET NOT NULL;
        ALTER TABLE late_event_audit ALTER COLUMN job_run_id SET NOT NULL;

        CREATE TABLE monitoring_queue_message (
            message_id uuid PRIMARY KEY,
            idempotency_key text NOT NULL UNIQUE,
            payload jsonb NOT NULL,
            status text NOT NULL DEFAULT 'READY',
            attempts integer NOT NULL DEFAULT 0,
            max_attempts integer NOT NULL DEFAULT 3,
            available_at timestamptz NOT NULL DEFAULT now(),
            claimed_at timestamptz,
            finished_at timestamptz,
            last_error_code text,
            last_error_class text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_monitoring_queue_status CHECK (
                status IN ('READY', 'PROCESSING', 'COMPLETED', 'DEAD_LETTER')
            ),
            CONSTRAINT ck_monitoring_queue_attempts CHECK (
                attempts >= 0 AND max_attempts > 0 AND attempts <= max_attempts
            ),
            CONSTRAINT ck_monitoring_queue_payload_object CHECK (
                jsonb_typeof(payload) = 'object'
            )
        );
        CREATE INDEX ix_monitoring_queue_claim
            ON monitoring_queue_message (available_at, created_at)
            WHERE status = 'READY';
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE monitoring_queue_message;
        ALTER TABLE late_event_audit ALTER COLUMN job_run_id DROP NOT NULL;
        ALTER TABLE window_aggregate ALTER COLUMN job_run_id DROP NOT NULL;
        ALTER TABLE canonical_event ALTER COLUMN job_run_id DROP NOT NULL;
        UPDATE canonical_event SET job_run_id = NULL
        WHERE job_run_id = '00000000-0000-0000-0000-000000000001';
        UPDATE window_aggregate SET job_run_id = NULL
        WHERE job_run_id = '00000000-0000-0000-0000-000000000001';
        UPDATE late_event_audit SET job_run_id = NULL
        WHERE job_run_id = '00000000-0000-0000-0000-000000000001';
        DELETE FROM job_run_log
        WHERE job_run_id = '00000000-0000-0000-0000-000000000001';
        """
    )
