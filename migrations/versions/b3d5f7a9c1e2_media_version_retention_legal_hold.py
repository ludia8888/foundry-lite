"""add media version retention mark + legal hold (L7)

Revision ID: b3d5f7a9c1e2
Revises: a7c9e1f3b5d7
Create Date: 2026-06-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3d5f7a9c1e2"
down_revision: str | Sequence[str] | None = "a7c9e1f3b5d7"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("media_item_versions", sa.Column("retention_marked_at", sa.String(), nullable=True))
    op.add_column(
        "media_item_versions",
        sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
