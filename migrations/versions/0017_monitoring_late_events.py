"""add late-event audit, aggregate counter and source watermarks"""

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE window_aggregate
            ADD COLUMN late_event_count integer NOT NULL DEFAULT 0,
            ADD CONSTRAINT ck_window_aggregate_late_nonnegative
                CHECK (late_event_count >= 0 AND late_event_count <= total_count)
        """
    )
    op.execute(
        """
        CREATE TABLE source_checkpoint (
            source_name text NOT NULL,
            region text NOT NULL,
            tenant_scope text NOT NULL DEFAULT '',
            watermark timestamptz NOT NULL,
            source_position jsonb NOT NULL DEFAULT '{}'::jsonb,
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (source_name, region, tenant_scope)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE late_event_audit (
            source_name text NOT NULL,
            event_id text NOT NULL,
            region text NOT NULL,
            tenant_scope text NOT NULL DEFAULT '',
            txn_id text NOT NULL,
            event_time timestamptz NOT NULL,
            watermark timestamptz NOT NULL,
            classification text NOT NULL,
            reason_code text NOT NULL,
            job_run_id uuid REFERENCES job_run_log(job_run_id),
            recorded_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (source_name, event_id),
            CONSTRAINT ck_late_event_audit_classification
                CHECK (classification = 'EXPIRED')
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE late_event_audit")
    op.execute("DROP TABLE source_checkpoint")
    op.execute(
        """
        ALTER TABLE window_aggregate
            DROP CONSTRAINT ck_window_aggregate_late_nonnegative,
            DROP COLUMN late_event_count
        """
    )
