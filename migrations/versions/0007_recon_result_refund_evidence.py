"""Keep refund evidence so financial summaries can be rebuilt on replay."""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Compatibility for databases that recorded revision 0006 before the old
    # refund migration was renumbered: Alembic considers 0006 applied, although
    # window_aggregate may not exist yet.
    op.execute("""
        CREATE TABLE IF NOT EXISTS window_aggregate (
            window_start timestamptz NOT NULL,
            window_end timestamptz NOT NULL,
            region varchar(16) NOT NULL,
            tenant_id varchar(100) NOT NULL DEFAULT '',
            provider varchar(100) NOT NULL,
            currency char(3) NOT NULL DEFAULT '',
            total_count integer NOT NULL,
            success_count integer NOT NULL,
            failed_count integer NOT NULL,
            pending_count integer NOT NULL,
            success_rate numeric(7, 6),
            computed_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (window_start, region, tenant_id, provider, currency),
            CHECK (window_end > window_start),
            CHECK (total_count >= 0),
            CHECK (success_count >= 0),
            CHECK (failed_count >= 0),
            CHECK (pending_count >= 0),
            CHECK (success_rate IS NULL OR success_rate BETWEEN 0 AND 1)
        )
    """)
    op.execute("""
        ALTER TABLE recon_result
            ADD COLUMN IF NOT EXISTS system_provider_txn_id text,
            ADD COLUMN IF NOT EXISTS system_status text,
            ADD COLUMN IF NOT EXISTS refund_of text,
            ADD COLUMN IF NOT EXISTS refunded_amount_minor numeric(38, 0)
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE recon_result
            DROP COLUMN refunded_amount_minor,
            DROP COLUMN refund_of,
            DROP COLUMN system_status,
            DROP COLUMN system_provider_txn_id
    """)
