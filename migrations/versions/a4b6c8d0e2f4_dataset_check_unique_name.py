"""add dataset quality check natural-key uniqueness

Revision ID: a4b6c8d0e2f4
Revises: f3a5c7d9e1b2
Create Date: 2026-06-22 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4b6c8d0e2f4"
down_revision: str | Sequence[str] | None = "f3a5c7d9e1b2"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_index(
        "uq_dataset_check_name",
        "dataset_checks",
        ["tenant_id", "dataset_id", "name"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
