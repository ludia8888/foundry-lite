"""expand Pipeline preview context and renewable execution lease

Revision ID: d9f1a3b5c7e0
Revises: c7e9a1b3d5f0
Create Date: 2026-07-28 17:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9f1a3b5c7e0"
down_revision: str | Sequence[str] | None = "c7e9a1b3d5f0"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pipeline_preview_runs",
        sa.Column("execution_context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("pipeline_preview_runs", sa.Column("execution_lease_token", sa.String(), nullable=True))
    op.add_column("pipeline_preview_runs", sa.Column("execution_lease_expires_at", sa.String(), nullable=True))
    op.add_column("pipeline_preview_runs", sa.Column("execution_heartbeat_at", sa.String(), nullable=True))
    op.create_index(
        "ix_pipeline_preview_recovery",
        "pipeline_preview_runs",
        ["status", "execution_lease_expires_at", "created_at"],
    )


def downgrade() -> None:
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
