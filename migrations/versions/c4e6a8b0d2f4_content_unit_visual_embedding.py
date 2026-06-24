"""add content_units visual embedding column (L11)

Revision ID: c4e6a8b0d2f4
Revises: b3d5f7a9c1e2
Create Date: 2026-06-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4e6a8b0d2f4"
down_revision: str | Sequence[str] | None = "b3d5f7a9c1e2"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # A video_frame_visual unit's CLIP image vector (list[float]); null for text units, which
    # embed at index time. Persisting it makes the visual index rebuildable from committed truth.
    op.add_column("content_units", sa.Column("embedding", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
