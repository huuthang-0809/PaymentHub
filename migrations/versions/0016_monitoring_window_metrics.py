"""expand window metrics for pending age, DQ and freshness

Revision ID: 0009_expand_window_metrics
Revises: 0008_event_identity_and_aggregate_grain
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE window_aggregate
            ADD COLUMN parse_error_count integer NOT NULL DEFAULT 0,
            ADD COLUMN non_terminal_count integer NOT NULL DEFAULT 0,
            ADD COLUMN pending_age_lt_5m_count integer NOT NULL DEFAULT 0,
            ADD COLUMN pending_age_5_to_15m_count integer NOT NULL DEFAULT 0,
            ADD COLUMN pending_age_15_to_60m_count integer NOT NULL DEFAULT 0,
            ADD COLUMN pending_age_gte_60m_count integer NOT NULL DEFAULT 0,
            ADD COLUMN oldest_pending_age_seconds bigint,
            ADD COLUMN freshness_lag_seconds bigint NOT NULL DEFAULT 0,
            ADD CONSTRAINT ck_window_aggregate_t8_nonnegative CHECK (
                parse_error_count >= 0 AND non_terminal_count >= 0
                AND pending_age_lt_5m_count >= 0
                AND pending_age_5_to_15m_count >= 0
                AND pending_age_15_to_60m_count >= 0
                AND pending_age_gte_60m_count >= 0
                AND oldest_pending_age_seconds >= 0
                AND freshness_lag_seconds >= 0
            ),
            ADD CONSTRAINT ck_window_aggregate_pending_age_total CHECK (
                pending_age_lt_5m_count + pending_age_5_to_15m_count
                + pending_age_15_to_60m_count + pending_age_gte_60m_count
                <= non_terminal_count
            )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE window_aggregate
            DROP CONSTRAINT ck_window_aggregate_pending_age_total,
            DROP CONSTRAINT ck_window_aggregate_t8_nonnegative,
            DROP COLUMN freshness_lag_seconds,
            DROP COLUMN oldest_pending_age_seconds,
            DROP COLUMN pending_age_gte_60m_count,
            DROP COLUMN pending_age_15_to_60m_count,
            DROP COLUMN pending_age_5_to_15m_count,
            DROP COLUMN pending_age_lt_5m_count,
            DROP COLUMN non_terminal_count,
            DROP COLUMN parse_error_count
        """
    )
