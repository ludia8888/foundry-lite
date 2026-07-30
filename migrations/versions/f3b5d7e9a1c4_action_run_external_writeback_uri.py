"""expand action_runs with external_writeback_uri write-ahead target

Revision ID: f3b5d7e9a1c4
Revises: e1a3c5d7f9b2
Create Date: 2026-07-30 00:00:00.000000

Persist the external-writeback target URI on the action run so a stranded ``external_pending``
run (a crash between the write-ahead commit and the resolve) can be recovered by HEADing the
external system. Additive and nullable, so old and new app code interoperate.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3b5d7e9a1c4"
down_revision: str | Sequence[str] | None = "e1a3c5d7f9b2"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("action_runs", sa.Column("external_writeback_uri", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
