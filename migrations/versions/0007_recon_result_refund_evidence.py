"""Keep refund evidence so financial summaries can be rebuilt on replay."""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
