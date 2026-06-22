"""add runtime run relations

Revision ID: d7e9f1a3b5c8
Revises: c6d8e0f2a4b6
Create Date: 2026-06-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7e9f1a3b5c8"
down_revision: str | Sequence[str] | None = "c6d8e0f2a4b6"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "runtime_run_relations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("source_run_type", sa.String(), nullable=False),
        sa.Column("source_run_id", sa.String(), nullable=False),
        sa.Column("target_run_type", sa.String(), nullable=False),
        sa.Column("target_run_id", sa.String(), nullable=False),
        sa.Column("relation", sa.String(), nullable=False),
        sa.Column("resource_type", sa.String(), nullable=False),
        sa.Column("resource_id", sa.String(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_run_type",
            "source_run_id",
            "target_run_type",
            "target_run_id",
            "relation",
            "resource_type",
            "resource_id",
            name="uq_runtime_run_relation",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
