"""expand durable Action notification policy registry

Revision ID: f4e6a8b0c2d5
Revises: f3d5e7a9b1c4
Create Date: 2026-08-05 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "f4e6a8b0c2d5"
down_revision: str | Sequence[str] | None = "f3d5e7a9b1c4"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _create_policy_table()
    _create_idempotency_table()
    if context.get_context().dialect.name == "postgresql":
        _enable_rls("action_notification_policies")
        _enable_rls("action_notification_policy_idempotency_records")


def _create_policy_table() -> None:
    op.create_table(
        "action_notification_policies",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("policy_name", sa.String(), nullable=False),
        sa.Column("target_ref", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("delivery_mode", sa.String(), nullable=False),
        sa.Column("recipients", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("config_fingerprint", sa.String(), nullable=False),
        sa.Column("created_by_user_id", sa.String(), nullable=False),
        sa.Column("updated_by_user_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.UniqueConstraint("tenant_id", "policy_name", name="uq_action_notification_policy_name"),
        sa.UniqueConstraint("tenant_id", "target_ref", name="uq_action_notification_policy_target"),
    )
    op.create_index(
        "ix_action_notification_policies_tenant_status",
        "action_notification_policies",
        ["tenant_id", "status", "policy_name"],
    )


def _create_idempotency_table() -> None:
    op.create_table(
        "action_notification_policy_idempotency_records",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("request_fingerprint", sa.String(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "actor_user_id",
            "operation",
            "idempotency_key",
            name="uq_action_notification_policy_idempotency",
        ),
    )
    op.create_index(
        "ix_action_notification_policy_idempotency_scope",
        "action_notification_policy_idempotency_records",
        ["tenant_id", "actor_user_id", "operation"],
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
