"""add ontology function types for first-class Functions on Objects

Revision ID: e2c3d4e5f6a7
Revises: d1b2c3d4e5f6
Create Date: 2026-07-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "e2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "d1b2c3d4e5f6"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "function_types",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("ontology_version_id", sa.String(), nullable=False),
        sa.Column("api_name", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "ontology_version_id", "api_name", name="uq_function_type_api"),
    )
    if context.get_context().dialect.name == "postgresql":
        op.execute("ALTER TABLE function_types ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE function_types FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY function_types_tenant_isolation ON function_types
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
