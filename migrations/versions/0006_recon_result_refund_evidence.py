"""Keep refund evidence so financial summaries can be rebuilt on replay."""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE recon_result
            ADD COLUMN system_provider_txn_id text,
            ADD COLUMN system_status text,
            ADD COLUMN refund_of text,
            ADD COLUMN refunded_amount_minor numeric(38, 0)
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE recon_result
            DROP COLUMN refunded_amount_minor,
            DROP COLUMN refund_of,
            DROP COLUMN system_status,
            DROP COLUMN system_provider_txn_id
    """)
