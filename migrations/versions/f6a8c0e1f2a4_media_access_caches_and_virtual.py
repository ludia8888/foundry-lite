"""add media access caches + virtual media sets (M9)

Revision ID: f6a8c0e1f2a4
Revises: e5a7b9d1f2a4
Create Date: 2026-06-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a8c0e1f2a4"
down_revision: str | Sequence[str] | None = "e5a7b9d1f2a4"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "media_sets",
        sa.Column("is_virtual", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "media_access_caches",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("source_media_item_version_id", sa.String(), nullable=False),
        sa.Column("access_pattern", sa.String(), nullable=False),
        sa.Column("access_pattern_spec_hash", sa.String(), nullable=False),
        sa.Column("persistence_policy", sa.String(), nullable=False),
        sa.Column("source_content_hash", sa.String(), nullable=False),
        sa.Column("blob_key", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("expires_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_media_item_version_id",
            "access_pattern",
            "access_pattern_spec_hash",
            name="uq_media_access_cache_key",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
