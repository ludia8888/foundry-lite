"""add insight review queue table

Revision ID: d1e2f3a4b5c6
Revises: c9d4e5f6a7b8
Create Date: 2026-06-19 13:55:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: str | Sequence[str] | None = "c9d4e5f6a7b8"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "insight_reviews",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("claim_id", sa.String(), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("priority", sa.String(), nullable=False),
        sa.Column("assignee_user_id", sa.String(), nullable=True),
        sa.Column("evidence_object_ids", sa.JSON(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("action_proposal", sa.JSON(), nullable=True),
        sa.Column("created_by_user_id", sa.String(), nullable=False),
        sa.Column("created_idempotency_key", sa.String(), nullable=False),
        sa.Column("assignment_idempotency_key", sa.String(), nullable=True),
        sa.Column("decision", sa.JSON(), nullable=True),
        sa.Column("decision_idempotency_key", sa.String(), nullable=True),
        sa.Column("review_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "created_idempotency_key", name="uq_insight_review_create_idempotency"),
        sa.UniqueConstraint(
            "tenant_id",
            "assignment_idempotency_key",
            name="uq_insight_review_assignment_idempotency",
        ),
        sa.UniqueConstraint("tenant_id", "decision_idempotency_key", name="uq_insight_review_decision_idempotency"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
