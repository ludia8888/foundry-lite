"""add workflow run ledger and DLQ replay lease

Revision ID: f3a5c7d9e1b2
Revises: e2f4a6b8c0d2
Create Date: 2026-06-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a5c7d9e1b2"
down_revision: str | Sequence[str] | None = "e2f4a6b8c0d2"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("dead_letter_records", sa.Column("replay_lease_token", sa.String(), nullable=True))
    op.add_column("dead_letter_records", sa.Column("replay_started_at", sa.String(), nullable=True))
    op.add_column("dead_letter_records", sa.Column("replay_lease_expires_at", sa.String(), nullable=True))
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("workflow_name", sa.String(), nullable=False),
        sa.Column("workflow_profile", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("request_fingerprint", sa.String(), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("dataset_id", sa.String(), nullable=True),
        sa.Column("audit_event_id", sa.String(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("started_at", sa.String(), nullable=True),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "workflow_name", "idempotency_key", name="uq_workflow_run_idempotency"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
