"""record dataset quality schema reference

Revision ID: b8f3c1d2e4a5
Revises: 4a6b9d0e1f2c
Create Date: 2026-06-19 00:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8f3c1d2e4a5"
down_revision: str | Sequence[str] | None = "4a6b9d0e1f2c"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "dataset_check_results",
        sa.Column(
            "validated_against_schema_version_id",
            sa.String(),
            nullable=False,
            server_default="legacy-unrecorded",
        ),
    )
    op.add_column(
        "dataset_check_results",
        sa.Column(
            "validated_against_schema_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
