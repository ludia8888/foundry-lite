"""add media derivatives + content units (PDF processing, M2)

Revision ID: d4f6a8c0e1f3
Revises: c3e5a7b9d1f2
Create Date: 2026-06-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4f6a8c0e1f3"
down_revision: str | Sequence[str] | None = "c3e5a7b9d1f2"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "media_derivatives",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("source_media_item_version_id", sa.String(), nullable=False),
        sa.Column("derivative_kind", sa.String(), nullable=False),
        sa.Column("processor_spec_hash", sa.String(), nullable=False),
        sa.Column("processor_name", sa.String(), nullable=False),
        sa.Column("processor_version", sa.String(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=True),
        sa.Column("model_version", sa.String(), nullable=False),
        sa.Column("params_hash", sa.String(), nullable=False),
        sa.Column("blob_key", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("mime_type", sa.String(), nullable=True),
        sa.Column("security_envelope", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("committed_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_media_item_version_id",
            "derivative_kind",
            "processor_spec_hash",
            "model_version",
            "params_hash",
            name="uq_media_derivative_spec",
        ),
    )
    op.create_table(
        "content_units",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("source_media_item_version_id", sa.String(), nullable=False),
        sa.Column("derivative_id", sa.String(), nullable=False),
        sa.Column("unit_kind", sa.String(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("start_ms", sa.Integer(), nullable=True),
        sa.Column("end_ms", sa.Integer(), nullable=True),
        sa.Column("bbox", sa.JSON(), nullable=True),
        sa.Column("speaker", sa.String(), nullable=True),
        sa.Column("language", sa.String(), nullable=True),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("text_hash", sa.String(), nullable=False),
        sa.Column("chunk_spec_hash", sa.String(), nullable=False),
        sa.Column("security_envelope", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_media_item_version_id",
            "unit_kind",
            "ordinal",
            "chunk_spec_hash",
            name="uq_content_unit_ordinal",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
