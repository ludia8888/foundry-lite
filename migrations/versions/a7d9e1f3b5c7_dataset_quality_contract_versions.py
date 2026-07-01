"""add dataset quality contract versions

Revision ID: a7d9e1f3b5c7
Revises: a6c8e0f2b4d7
Create Date: 2026-07-01 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "a7d9e1f3b5c7"
down_revision: str | Sequence[str] | None = "a6c8e0f2b4d7"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("dataset_check_results", sa.Column("data_contract_version_id", sa.String(), nullable=True))
    op.create_table(
        "dataset_quality_contract_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("dataset_id", sa.String(), nullable=False),
        sa.Column("contract_key", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("owner_user_id", sa.String(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("checks_snapshot", sa.JSON(), nullable=False),
        sa.Column("schema_version_id", sa.String(), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("activated_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "contract_key",
            "version",
            name="uq_dataset_quality_contract_version",
        ),
    )
    if context.get_context().dialect.name == "postgresql":
        op.execute("ALTER TABLE dataset_quality_contract_versions ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE dataset_quality_contract_versions FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY dataset_quality_contract_versions_tenant_isolation ON dataset_quality_contract_versions
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
