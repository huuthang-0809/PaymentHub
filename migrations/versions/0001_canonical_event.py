from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("""
        CREATE TABLE ingest_message (
            event_id uuid PRIMARY KEY,
            event_type text NOT NULL,
            payload_hash text NOT NULL,
            accepted_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    # canonical_event save paymenthub transactions
    op.execute("""
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
        ) PARTITION BY RANGE (event_day)
    """)
    op.execute("CREATE INDEX ix_canonical_scope_time ON canonical_event (region, tenant_id, provider, updated_at DESC)")
    op.execute("CREATE INDEX ix_canonical_updated_brin ON canonical_event USING brin (updated_at)")

def downgrade() -> None:
    op.execute("DROP TABLE canonical_event CASCADE")
    op.execute("DROP TABLE ingest_message")