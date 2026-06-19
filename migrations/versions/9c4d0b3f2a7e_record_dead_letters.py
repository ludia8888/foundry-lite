"""add record-level dead letter table

Revision ID: 9c4d0b3f2a7e
Revises: 1303e658cebe
Create Date: 2026-06-18 20:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9c4d0b3f2a7e"
down_revision: str | Sequence[str] | None = "1303e658cebe"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "dead_letter_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("source_event_id", sa.String(), nullable=False),
        sa.Column("source_dataset_version_id", sa.String(), nullable=True),
        sa.Column("source_run_id", sa.String(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=True),
        sa.Column("transform_version", sa.String(), nullable=True),
        sa.Column("error_kind", sa.String(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("event_time", sa.String(), nullable=True),
        sa.Column("ingested_at", sa.String(), nullable=False),
        sa.Column("first_failed_at", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("replay_status", sa.String(), nullable=False),
        sa.Column("replay_run_id", sa.String(), nullable=True),
        sa.Column("replay_idempotency_key", sa.String(), nullable=True),
        sa.Column("replay_requested_at", sa.String(), nullable=True),
        sa.Column("discarded_at", sa.String(), nullable=True),
        sa.Column("backfill_plan", sa.JSON(), nullable=True),
        sa.Column("is_closed_partition_affected", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_event_id",
            "payload_hash",
            name="uq_dead_letter_record_source_payload",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
