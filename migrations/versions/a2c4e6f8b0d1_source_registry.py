"""add tenant-scoped product Source onboarding registry

Revision ID: a2c4e6f8b0d1
Revises: e8c2a4b6d9f1
Create Date: 2026-06-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "a2c4e6f8b0d1"
down_revision: str | Sequence[str] | None = "e8c2a4b6d9f1"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "source_connections",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("source_name", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("target_dataset_ref", sa.String(), nullable=True),
        sa.Column("target_media_set_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("config_summary", sa.JSON(), nullable=False),
        sa.Column("config_fingerprint", sa.String(), nullable=False),
        sa.Column("last_run_id", sa.String(), nullable=True),
        sa.Column("last_workflow_run_id", sa.String(), nullable=True),
        sa.Column("last_commit_ref", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "source_name", name="uq_source_connection_name"),
    )
    if context.get_context().dialect.name == "postgresql":
        op.execute("ALTER TABLE source_connections ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE source_connections FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY source_connections_tenant_isolation ON source_connections
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
