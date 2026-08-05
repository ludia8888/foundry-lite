"""expand branch-isolated Action link overlays

Revision ID: f2c4d6e8a0b3
Revises: f1b3c5d7e9a2
Create Date: 2026-08-04 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "f2c4d6e8a0b3"
down_revision: str | Sequence[str] | None = "f1b3c5d7e9a2"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "action_branch_links",
        *_columns(),
        sa.UniqueConstraint(
            "tenant_id",
            "branch_id",
            "link_type_api_name",
            "from_object_id",
            "to_object_id",
            name="uq_action_branch_link",
        ),
    )
    op.create_index("ix_action_branch_links_scope", "action_branch_links", ["tenant_id", "branch_id"])
    if context.get_context().dialect.name == "postgresql":
        _enable_rls("action_branch_links")


def _columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("branch_id", sa.String(), nullable=False),
        sa.Column("link_type_id", sa.String(), nullable=False),
        sa.Column("link_type_api_name", sa.String(), nullable=False),
        sa.Column("from_object_type_id", sa.String(), nullable=False),
        sa.Column("from_api_name", sa.String(), nullable=False),
        sa.Column("from_object_id", sa.String(), nullable=False),
        sa.Column("to_object_type_id", sa.String(), nullable=False),
        sa.Column("to_api_name", sa.String(), nullable=False),
        sa.Column("to_object_id", sa.String(), nullable=False),
        sa.Column("base_link_version", sa.Integer(), nullable=True),
        sa.Column("overlay_version", sa.Integer(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("last_action_run_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )


def _enable_rls(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('foundry_lite.tenant_id', true)) "
            "WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))"
        )
    )


def downgrade() -> None:
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
