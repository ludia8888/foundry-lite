"""expand governed Action effect receipts

Revision ID: b5d7f9a1c3e6
Revises: a4c6e8f0b2d5
Create Date: 2026-08-03 01:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "b5d7f9a1c3e6"
down_revision: str | Sequence[str] | None = "a4c6e8f0b2d5"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "action_effect_receipts",
        *_columns(),
        sa.UniqueConstraint("tenant_id", "action_run_id", "effect_id", name="uq_action_effect_receipt"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_action_effect_idempotency"),
    )
    op.create_index(
        "ix_action_effect_receipts_tenant_status",
        "action_effect_receipts",
        ["tenant_id", "status", "retry_at"],
    )
    op.create_index(
        "ix_action_effect_receipts_tenant_run",
        "action_effect_receipts",
        ["tenant_id", "action_run_id"],
    )
    if context.get_context().dialect.name == "postgresql":
        _enable_rls()


def _columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("action_run_id", sa.String(), nullable=False),
        sa.Column("effect_id", sa.String(), nullable=False),
        sa.Column("phase", sa.String(), nullable=False),
        sa.Column("effect_kind", sa.String(), nullable=False),
        sa.Column("target_ref", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(), nullable=True),
        sa.Column("lease_token", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.String(), nullable=True),
        sa.Column("fencing_token", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("heartbeat_at", sa.String(), nullable=True),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("response", sa.JSON(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("retry_at", sa.String(), nullable=True),
        sa.Column("external_execution_id", sa.String(), nullable=True),
        sa.Column("outbox_event_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=True),
    )


def _enable_rls() -> None:
    table = "action_effect_receipts"
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
