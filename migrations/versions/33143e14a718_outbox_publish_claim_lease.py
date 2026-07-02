"""add outbox publish claim lease timestamp

Revision ID: 33143e14a718
Revises: e3f5a7c9d1b3
Create Date: 2026-07-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "33143e14a718"
down_revision: str | Sequence[str] | None = "e3f5a7c9d1b3"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("outbox_events", sa.Column("claimed_at", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("Outbox publish claim lease uses forward-fix-only schema changes.")
