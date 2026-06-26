"""add AIP action proposal fields to insight reviews

Revision ID: a8c0e2f4b6d9
Revises: f7a9c1e3b5d8
Create Date: 2026-06-25 21:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8c0e2f4b6d9"
down_revision: str | Sequence[str] | None = "f7a9c1e3b5d8"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("insight_reviews", sa.Column("proposal_type", sa.String(), nullable=True))
    op.add_column("insight_reviews", sa.Column("proposal_fingerprint", sa.String(), nullable=True))
    op.add_column("insight_reviews", sa.Column("originating_ai_run_id", sa.String(), nullable=True))
    op.add_column("insight_reviews", sa.Column("originating_tool_call_id", sa.String(), nullable=True))
    op.add_column("insight_reviews", sa.Column("expires_at", sa.String(), nullable=True))
    op.add_column("insight_reviews", sa.Column("execution_status", sa.String(), nullable=True))
    op.add_column("insight_reviews", sa.Column("approved_action_run_id", sa.String(), nullable=True))
    op.add_column("insight_reviews", sa.Column("approval_policy_version", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
