"""expand normalized Action log and revert evidence

Revision ID: c6e8f0b2d4a7
Revises: b5d7f9a1c3e6
Create Date: 2026-08-03 04:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "c6e8f0b2d4a7"
down_revision: str | Sequence[str] | None = "b5d7f9a1c3e6"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("object_edits", sa.Column("revert_payload", sa.JSON(), nullable=True))
    op.create_table(
        "action_log_entries",
        *_entry_columns(),
        sa.UniqueConstraint("tenant_id", "action_run_id", name="uq_action_log_run"),
        sa.UniqueConstraint("tenant_id", "log_object_type_api_name", "log_object_id", name="uq_action_log_object"),
    )
    op.create_index("ix_action_log_entries_tenant_created", "action_log_entries", ["tenant_id", "created_at"])
    op.create_table(
        "action_log_objects",
        *_object_columns(),
        sa.UniqueConstraint("tenant_id", "action_log_entry_id", "object_edit_id", name="uq_action_log_edit"),
    )
    op.create_index("ix_action_log_objects_tenant_log", "action_log_objects", ["tenant_id", "action_log_entry_id"])
    if context.get_context().dialect.name == "postgresql":
        _enable_rls("action_log_entries")
        _enable_rls("action_log_objects")


def _entry_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("action_run_id", sa.String(), nullable=False),
        sa.Column("log_object_type_api_name", sa.String(), nullable=False),
        sa.Column("log_object_id", sa.String(), nullable=False),
        sa.Column("action_type_id", sa.String(), nullable=False),
        sa.Column("action_type_api_name", sa.String(), nullable=False),
        sa.Column("definition_version", sa.String(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("branch_id", sa.String(), nullable=True),
        sa.Column("plan_hash", sa.String(), nullable=True),
        sa.Column("approval_id", sa.String(), nullable=True),
        sa.Column("revert_allowed", sa.Boolean(), nullable=False),
        sa.Column("revert_status", sa.String(), nullable=False, server_default="eligible"),
        sa.Column("reverted_by_run_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=False),
    )


def _object_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("action_log_entry_id", sa.String(), nullable=False),
        sa.Column("object_edit_id", sa.String(), nullable=False),
        sa.Column("object_type_id", sa.String(), nullable=False),
        sa.Column("object_type_api_name", sa.String(), nullable=False),
        sa.Column("object_id", sa.String(), nullable=False),
        sa.Column("edit_type", sa.String(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
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
