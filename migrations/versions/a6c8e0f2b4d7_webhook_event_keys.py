"""add webhook event key index

Revision ID: a6c8e0f2b4d7
Revises: b4d6f8a0c2e4
Create Date: 2026-06-29 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "a6c8e0f2b4d7"
down_revision: str | Sequence[str] | None = "b4d6f8a0c2e4"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "webhook_event_keys",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("dataset_id", sa.String(), nullable=False),
        sa.Column("connector_name", sa.String(), nullable=False),
        sa.Column("resource_name", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("transaction_id", sa.String(), nullable=False),
        sa.Column("committed_version_id", sa.String(), nullable=False),
        sa.Column("payload_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "connector_name",
            "resource_name",
            "event_id",
            name="uq_webhook_event_key",
        ),
    )
    if context.get_context().dialect.name == "postgresql":
        op.execute("ALTER TABLE webhook_event_keys ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE webhook_event_keys FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY webhook_event_keys_tenant_isolation ON webhook_event_keys
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
