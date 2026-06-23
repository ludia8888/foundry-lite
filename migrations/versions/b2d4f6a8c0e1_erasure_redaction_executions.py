"""add durable erasure redaction execution tracking

Revision ID: b2d4f6a8c0e1
Revises: a1b2c3d4e5f6
Create Date: 2026-06-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2d4f6a8c0e1"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "erasure_redaction_executions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("resource_type", sa.String(), nullable=False),
        sa.Column("resource_id", sa.String(), nullable=False),
        sa.Column("surface", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("new_version_id", sa.String(), nullable=True),
        sa.Column("executed_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "request_id",
            "resource_type",
            "resource_id",
            name="uq_erasure_redaction_execution_resource",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
