"""add tenant-scoped REST connector onboarding registry

Revision ID: e8c2a4b6d9f1
Revises: f9b1c3d5e7a9
Create Date: 2026-06-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "e8c2a4b6d9f1"
down_revision: str | Sequence[str] | None = "f9b1c3d5e7a9"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "connector_connections",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("connector_name", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("auth", sa.JSON(), nullable=False),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=True),
        sa.Column("allow_private_network", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("config_fingerprint", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "connector_name", name="uq_connector_connection_name"),
    )
    op.create_table(
        "connector_resources",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("connector_name", sa.String(), nullable=False),
        sa.Column("resource_name", sa.String(), nullable=False),
        sa.Column("dataset_ref", sa.String(), nullable=False),
        sa.Column("resource_path", sa.Text(), nullable=False),
        sa.Column("pagination", sa.JSON(), nullable=False),
        sa.Column("schema_columns", sa.JSON(), nullable=False),
        sa.Column("primary_key", sa.JSON(), nullable=False),
        sa.Column("config_fingerprint", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "connector_name",
            "resource_name",
            name="uq_connector_resource_name",
        ),
    )
    if context.get_context().dialect.name == "postgresql":
        op.execute("ALTER TABLE connector_connections ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE connector_connections FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY connector_connections_tenant_isolation ON connector_connections
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )
        op.execute("ALTER TABLE connector_resources ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE connector_resources FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY connector_resources_tenant_isolation ON connector_resources
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
