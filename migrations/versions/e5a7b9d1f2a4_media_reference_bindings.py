"""add media reference bindings (ontology media_reference property, M4)

Revision ID: e5a7b9d1f2a4
Revises: d4f6a8c0e1f3
Create Date: 2026-06-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5a7b9d1f2a4"
down_revision: str | Sequence[str] | None = "d4f6a8c0e1f3"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "media_reference_bindings",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("holder_type", sa.String(), nullable=False),
        sa.Column("holder_id", sa.String(), nullable=False),
        sa.Column("property_name", sa.String(), nullable=False),
        sa.Column("media_set_id", sa.String(), nullable=False),
        sa.Column("media_item_id", sa.String(), nullable=False),
        sa.Column("media_item_version_id", sa.String(), nullable=False),
        sa.Column("logical_path", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("security_envelope", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "holder_type", "holder_id", "property_name", name="uq_media_reference_binding"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
