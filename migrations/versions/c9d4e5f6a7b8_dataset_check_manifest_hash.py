"""record dataset check candidate manifest hash

Revision ID: c9d4e5f6a7b8
Revises: b8f3c1d2e4a5
Create Date: 2026-06-19 01:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b8f3c1d2e4a5"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "dataset_check_results",
        sa.Column("checked_manifest_hash", sa.String(), nullable=False, server_default="legacy-unrecorded"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
