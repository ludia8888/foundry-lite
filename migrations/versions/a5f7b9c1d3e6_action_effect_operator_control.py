"""expand durable Action effect operator control

Revision ID: a5f7b9c1d3e6
Revises: f4e6a8b0c2d5
Create Date: 2026-08-05 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "a5f7b9c1d3e6"
down_revision: str | Sequence[str] | None = "f4e6a8b0c2d5"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _expand_receipts()
    _create_operation_table()
    if context.get_context().dialect.name == "postgresql":
        _enable_rls("action_effect_operation_requests")


def _expand_receipts() -> None:
    op.add_column("action_effect_receipts", sa.Column("dispatch_started_at", sa.String()))
    op.add_column("action_effect_receipts", sa.Column("cancel_requested_at", sa.String()))
    op.add_column("action_effect_receipts", sa.Column("cancel_reason", sa.String()))
    op.add_column("action_effect_receipts", sa.Column("reconciled_at", sa.String()))
    op.add_column("action_effect_receipts", sa.Column("reconciled_by_user_id", sa.String()))
    op.add_column("action_effect_receipts", sa.Column("reconciliation", sa.JSON()))


def _create_operation_table() -> None:
    op.create_table(
        "action_effect_operation_requests",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("actor_user_id", sa.String(), nullable=False),
        sa.Column("receipt_id", sa.String(), nullable=False),
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
            name="uq_action_effect_operation_idempotency",
        ),
    )
    op.create_index(
        "ix_action_effect_operation_requests_scope",
        "action_effect_operation_requests",
        ["tenant_id", "receipt_id", "created_at"],
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
