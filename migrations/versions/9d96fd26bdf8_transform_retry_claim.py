"""add transform run retry claim timestamp

Revision ID: 9d96fd26bdf8
Revises: 33143e14a718
Create Date: 2026-07-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9d96fd26bdf8"
down_revision: str | Sequence[str] | None = "33143e14a718"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("transform_runs", sa.Column("retried_at", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("Transform retry claim uses forward-fix-only schema changes.")
