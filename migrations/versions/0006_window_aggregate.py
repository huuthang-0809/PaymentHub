"""Create five-minute monitoring aggregates."""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE window_aggregate (
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
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE window_aggregate")
