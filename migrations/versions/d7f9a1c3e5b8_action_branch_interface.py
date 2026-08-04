"""expand Action interface targets and branch overlays

Revision ID: d7f9a1c3e5b8
Revises: c6e8f0b2d4a7
Create Date: 2026-08-03 17:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "d7f9a1c3e5b8"
down_revision: str | Sequence[str] | None = "c6e8f0b2d4a7"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "action_types",
        sa.Column("target_kind", sa.String(), nullable=False, server_default="object"),
    )
    op.add_column("action_runs", sa.Column("branch_id", sa.String(), nullable=True))
    op.create_table(
        "action_branch_objects",
        *_object_columns(),
        sa.UniqueConstraint(
            "tenant_id", "branch_id", "object_type_api_name", "object_id", name="uq_action_branch_object"
        ),
    )
    op.create_index("ix_action_branch_objects_scope", "action_branch_objects", ["tenant_id", "branch_id"])
    op.create_table(
        "action_branch_edits",
        *_edit_columns(),
        sa.UniqueConstraint("tenant_id", "branch_id", "operation_key", name="uq_action_branch_edit_operation"),
    )
    op.create_index("ix_action_branch_edits_scope", "action_branch_edits", ["tenant_id", "branch_id"])
    if context.get_context().dialect.name == "postgresql":
        _enable_rls("action_branch_objects")
        _enable_rls("action_branch_edits")


def _object_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("branch_id", sa.String(), nullable=False),
        sa.Column("object_type_id", sa.String(), nullable=False),
        sa.Column("object_type_api_name", sa.String(), nullable=False),
        sa.Column("object_id", sa.String(), nullable=False),
        sa.Column("base_object_version", sa.Integer(), nullable=True),
        sa.Column("overlay_version", sa.Integer(), nullable=False),
        sa.Column("properties", sa.JSON(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("last_action_run_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )


def _edit_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("branch_id", sa.String(), nullable=False),
        sa.Column("action_run_id", sa.String(), nullable=False),
        sa.Column("operation_key", sa.String(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("edit_kind", sa.String(), nullable=False),
        sa.Column("object_type_id", sa.String(), nullable=False),
        sa.Column("object_type_api_name", sa.String(), nullable=False),
        sa.Column("object_id", sa.String(), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
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
