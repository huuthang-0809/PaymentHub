"""Align canonical_event with the canonical domain and monitoring queries."""

from alembic import op


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE canonical_event RENAME COLUMN event_day TO event_date")
    op.execute(
        """
        ALTER TABLE canonical_event
            ADD COLUMN amount_status text NOT NULL DEFAULT 'OK',
            ADD COLUMN created_at timestamptz,
            ADD COLUMN is_terminal boolean NOT NULL DEFAULT false,
            ADD COLUMN is_money_movement boolean NOT NULL DEFAULT false,
            ADD COLUMN is_reconcilable boolean NOT NULL DEFAULT true,
            ADD COLUMN is_deleted boolean NOT NULL DEFAULT false,
            ADD COLUMN is_test_data boolean NOT NULL DEFAULT false,
            ADD COLUMN raw jsonb NOT NULL DEFAULT '{}'::jsonb
        """
    )
    op.execute("ALTER TABLE canonical_event ALTER COLUMN updated_at DROP NOT NULL")
    op.execute("UPDATE canonical_event SET created_at = updated_at")
    op.execute("ALTER TABLE canonical_event ALTER COLUMN created_at SET NOT NULL")
    op.execute(
        """
        ALTER TABLE canonical_event
            ADD CONSTRAINT ck_canonical_event_amount_status
                CHECK (amount_status IN (
                    'OK', 'ASSUMED_CURRENCY', 'NO_CURRENCY',
                    'INVALID', 'NOT_APPLICABLE'
                )),
            ADD CONSTRAINT ck_canonical_event_asia_tenant
                CHECK (region <> 'ASIA' OR tenant_id IS NOT NULL)
        """
    )
    op.execute(
        """
        DO $$
        DECLARE
            partition_day date;
        BEGIN
            FOR partition_day IN
                SELECT generate_series(
                    current_date - 7, current_date + 7, interval '1 day'
                )::date
            LOOP
                EXECUTE format(
                    'CREATE TABLE IF NOT EXISTS canonical_event_%s '
                    'PARTITION OF canonical_event FOR VALUES FROM (%L) TO (%L)',
                    to_char(partition_day, 'YYYYMMDD'),
                    partition_day,
                    partition_day + 1
                );
            END LOOP;
        END $$
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS canonical_event_default
        PARTITION OF canonical_event DEFAULT
        """
    )
    op.execute(
        """
        CREATE INDEX ix_canonical_event_monitoring
        ON canonical_event (region, event_date, status)
        WHERE is_deleted = false AND is_test_data = false
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_canonical_event_monitoring")
    op.execute("DROP TABLE IF EXISTS canonical_event_default")
    op.execute(
        """
        DO $$
        DECLARE
            partition_record record;
        BEGIN
            FOR partition_record IN
                SELECT child.relname AS name
                FROM pg_inherits
                JOIN pg_class parent ON pg_inherits.inhparent = parent.oid
                JOIN pg_class child ON pg_inherits.inhrelid = child.oid
                WHERE parent.relname = 'canonical_event'
            LOOP
                EXECUTE format('DROP TABLE IF EXISTS %I', partition_record.name);
            END LOOP;
        END $$
        """
    )
    op.execute(
        """
        ALTER TABLE canonical_event
            DROP CONSTRAINT ck_canonical_event_asia_tenant,
            DROP CONSTRAINT ck_canonical_event_amount_status,
            DROP COLUMN raw, DROP COLUMN is_test_data, DROP COLUMN is_deleted,
            DROP COLUMN is_reconcilable, DROP COLUMN is_money_movement,
            DROP COLUMN is_terminal, DROP COLUMN created_at,
            DROP COLUMN amount_status
        """
    )
    op.execute("ALTER TABLE canonical_event ALTER COLUMN updated_at SET NOT NULL")
    op.execute("ALTER TABLE canonical_event RENAME COLUMN event_date TO event_day")
