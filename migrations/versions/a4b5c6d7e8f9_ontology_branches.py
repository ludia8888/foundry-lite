"""add ontology_branches for isolated ontology editing (true ontology branching)

Revision ID: a4b5c6d7e8f9
Revises: e2c3d4e5f6a7
Create Date: 2026-07-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "a4b5c6d7e8f9"
down_revision: str | Sequence[str] | None = "e2c3d4e5f6a7"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "ontology_branches",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("base_version_id", sa.String(), nullable=False),
        sa.Column("base_version_number", sa.Integer(), nullable=False),
        sa.Column("base_yaml_text", sa.Text(), nullable=False),
        sa.Column("content_yaml_text", sa.Text(), nullable=False),
        sa.Column("content_fingerprint", sa.String(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("rebased_at", sa.String(), nullable=True),
        sa.Column("merged_proposal_id", sa.String(), nullable=True),
        sa.Column("merged_version_number", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # Name uniqueness applies only among OPEN branches per tenant (a merged or
    # abandoned branch frees its name), so the service enforces it with a
    # conditional insert; this plain index keeps that lookup cheap.
    op.create_index(
        "ix_ontology_branches_tenant_name",
        "ontology_branches",
        ["tenant_id", "name"],
    )
    if context.get_context().dialect.name == "postgresql":
        op.execute("ALTER TABLE ontology_branches ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE ontology_branches FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY ontology_branches_tenant_isolation ON ontology_branches
            USING (tenant_id = current_setting('foundry_lite.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))
            """
        )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
