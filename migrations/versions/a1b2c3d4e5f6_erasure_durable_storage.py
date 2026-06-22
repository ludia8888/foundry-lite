"""add durable erasure requests and certificates

Revision ID: a1b2c3d4e5f6
Revises: d7e9f1a3b5c8
Create Date: 2026-06-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "d7e9f1a3b5c8"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "erasure_requests",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("subject_kind", sa.String(), nullable=False),
        sa.Column("subject_hash", sa.String(), nullable=False),
        sa.Column("subject_token_key_id", sa.String(), nullable=False),
        sa.Column("requested_by", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("manifest_id", sa.String(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("requested_at", sa.String(), nullable=False),
        sa.Column("executed_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_erasure_requests_tenant_idempotency"),
    )
    op.create_table(
        "erasure_certificates",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("manifest_id", sa.String(), nullable=False),
        sa.Column("certificate", sa.JSON(), nullable=False),
        sa.Column("certified_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "request_id", name="uq_erasure_certificates_tenant_request"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
