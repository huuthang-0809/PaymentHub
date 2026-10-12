"""Add safe rule snapshots to provider-health evaluation runs.

Revision ID: 0014
Revises: 0013
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE health_evaluation_run
            ADD COLUMN reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN evaluations jsonb NOT NULL DEFAULT '[]'::jsonb,
            ADD CONSTRAINT ck_health_evaluation_run_reasons_array
                CHECK (jsonb_typeof(reasons) = 'array'),
            ADD CONSTRAINT ck_health_evaluation_run_evaluations_array
                CHECK (jsonb_typeof(evaluations) = 'array')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE health_evaluation_run
            DROP CONSTRAINT ck_health_evaluation_run_evaluations_array,
            DROP CONSTRAINT ck_health_evaluation_run_reasons_array,
            DROP COLUMN evaluations,
            DROP COLUMN reasons
        """
    )
