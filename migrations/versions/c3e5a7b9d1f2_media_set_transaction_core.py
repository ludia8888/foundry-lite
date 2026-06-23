"""add local MediaSet transaction core (sets/transactions/items/versions)

Revision ID: c3e5a7b9d1f2
Revises: b2d4f6a8c0e1
Create Date: 2026-06-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3e5a7b9d1f2"
down_revision: str | Sequence[str] | None = "b2d4f6a8c0e1"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "media_sets",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("namespace", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("schema_type", sa.String(), nullable=False),
        sa.Column("primary_format", sa.String(), nullable=False),
        sa.Column("allowed_input_formats", sa.JSON(), nullable=False),
        sa.Column("transaction_policy", sa.String(), nullable=False),
        sa.Column("storage_profile", sa.String(), nullable=False),
        sa.Column("processing_profile", sa.String(), nullable=False),
        sa.Column("classification", sa.String(), nullable=False),
        sa.Column("retention_policy_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "namespace", "name", name="uq_media_set_ref"),
    )
    op.create_table(
        "media_transactions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("media_set_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("request_fingerprint", sa.String(), nullable=False),
        sa.Column("opened_at", sa.String(), nullable=False),
        sa.Column("committed_at", sa.String(), nullable=True),
        sa.Column("aborted_at", sa.String(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("trace", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "media_set_id", "idempotency_key", name="uq_media_transaction_idempotency"),
    )
    op.create_table(
        "media_items",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("media_set_id", sa.String(), nullable=False),
        sa.Column("logical_path", sa.String(), nullable=False),
        sa.Column("head_version_id", sa.String(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "media_set_id", "logical_path", name="uq_media_item_path"),
    )
    op.create_table(
        "media_item_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("media_item_id", sa.String(), nullable=False),
        sa.Column("media_transaction_id", sa.String(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("blob_key", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("supplied_mime_type", sa.String(), nullable=False),
        sa.Column("sniffed_mime_type", sa.String(), nullable=False),
        sa.Column("schema_type", sa.String(), nullable=False),
        sa.Column("format", sa.String(), nullable=False),
        sa.Column("probe_metadata", sa.JSON(), nullable=False),
        sa.Column("security_envelope", sa.JSON(), nullable=False),
        sa.Column("source_ref", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("committed_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("media_item_id", "version_number", name="uq_media_item_version_number"),
        sa.UniqueConstraint("tenant_id", "content_hash", "byte_size", "blob_key", name="uq_media_version_blob"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
