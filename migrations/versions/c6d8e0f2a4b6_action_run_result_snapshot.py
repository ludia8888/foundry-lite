"""add action run result snapshot

Revision ID: c6d8e0f2a4b6
Revises: b5c7d9e1f3a6
Create Date: 2026-06-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c6d8e0f2a4b6"
down_revision: str | Sequence[str] | None = "b5c7d9e1f3a6"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("action_runs", sa.Column("result", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
