"""add dataset layout policy metadata

Revision ID: 4a6b9d0e1f2c
Revises: 9c4d0b3f2a7e
Create Date: 2026-06-18 23:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4a6b9d0e1f2c"
down_revision: str | Sequence[str] | None = "9c4d0b3f2a7e"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("datasets", sa.Column("partition_spec", sa.JSON(), nullable=True))
    op.add_column("datasets", sa.Column("sort_order", sa.JSON(), nullable=True))
    op.add_column("datasets", sa.Column("target_file_size_bytes", sa.Integer(), nullable=True))
    op.execute("UPDATE datasets SET partition_spec = '[]' WHERE partition_spec IS NULL")
    op.execute("UPDATE datasets SET sort_order = '[]' WHERE sort_order IS NULL")


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
