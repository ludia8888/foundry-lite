"""add object index row hashes for changelog incremental reindex

Revision ID: c0a1b2c3d4e5
Revises: 9d96fd26bdf8
Create Date: 2026-07-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "c0a1b2c3d4e5"
down_revision: str | Sequence[str] | None = "9d96fd26bdf8"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "object_index_row_hashes",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("object_type_id", sa.String(), nullable=False),
        sa.Column("object_id", sa.String(), nullable=False),
        sa.Column("row_hash", sa.String(), nullable=False),
        sa.Column("dataset_version_id", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "object_type_id", "object_id", name="uq_object_index_row_hash"),
    )
    if context.get_context().dialect.name == "postgresql":
        op.execute("ALTER TABLE object_index_row_hashes ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE object_index_row_hashes FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY object_index_row_hashes_tenant_isolation ON object_index_row_hashes
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
