"""add transform run definition snapshot

Revision ID: e2f4a6b8c0d2
Revises: d1e2f3a4b5c6
Create Date: 2026-06-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2f4a6b8c0d2"
down_revision: str | Sequence[str] | None = "d1e2f3a4b5c6"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("transform_runs", sa.Column("definition_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
