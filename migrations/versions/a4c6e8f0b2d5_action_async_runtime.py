"""expand Action runs with durable orchestration and fencing evidence

Revision ID: a4c6e8f0b2d5
Revises: f3b5d7e9a1c4
Create Date: 2026-08-03 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "a4c6e8f0b2d5"
down_revision: str | Sequence[str] | None = "f3b5d7e9a1c4"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _extend_action_runs()
    _create_action_run_steps()
    _create_action_step_attempts()
    _create_action_run_events()
    if context.get_context().dialect.name == "postgresql":
        for table in ("action_run_steps", "action_step_attempts", "action_run_events"):
            _enable_rls(table)


def _extend_action_runs() -> None:
    for column in _action_run_nullable_columns():
        op.add_column("action_runs", column)
    op.add_column("action_runs", sa.Column("execution_mode", sa.String(), nullable=False, server_default="sync"))
    op.add_column(
        "action_runs", sa.Column("dispatch_status", sa.String(), nullable=False, server_default="not_required")
    )
    op.add_column(
        "action_runs", sa.Column("dispatch_attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0"))
    )
    op.add_column("action_runs", sa.Column("event_sequence", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.create_index("ix_action_runs_tenant_created", "action_runs", ["tenant_id", "created_at", "id"])
    op.create_index("ix_action_runs_tenant_dispatch", "action_runs", ["tenant_id", "dispatch_status"])


def _action_run_nullable_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("definition_version", sa.String(), nullable=True),
        sa.Column("plan_hash", sa.String(), nullable=True),
        sa.Column("execution_plan", sa.JSON(), nullable=True),
        sa.Column("workflow_run_id", sa.String(), nullable=True),
        sa.Column("dispatch_error", sa.JSON(), nullable=True),
        sa.Column("cancel_requested_at", sa.String(), nullable=True),
        sa.Column("cancel_reason", sa.String(), nullable=True),
        sa.Column("cancel_idempotency_key", sa.String(), nullable=True),
        sa.Column("cancel_request_fingerprint", sa.String(), nullable=True),
        sa.Column("started_at", sa.String(), nullable=True),
        sa.Column("updated_at", sa.String(), nullable=True),
    )


def _create_action_run_steps() -> None:
    op.create_table(
        "action_run_steps",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("step_key", sa.String(), nullable=False),
        sa.Column("step_kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("input_manifest", sa.JSON(), nullable=False),
        sa.Column("output_manifest", sa.JSON(), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.String(), nullable=True),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.UniqueConstraint("tenant_id", "run_id", "step_key", name="uq_action_run_step_key"),
    )
    op.create_index("ix_action_run_steps_tenant_run", "action_run_steps", ["tenant_id", "run_id"])


def _create_action_step_attempts() -> None:
    op.create_table(
        "action_step_attempts",
        *_attempt_columns(),
        sa.UniqueConstraint("tenant_id", "step_id", "attempt_number", name="uq_action_step_attempt_number"),
        sa.UniqueConstraint("tenant_id", "step_id", "fencing_token", name="uq_action_step_fencing_token"),
    )
    op.create_index("ix_action_step_attempts_tenant_step", "action_step_attempts", ["tenant_id", "step_id"])


def _attempt_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("step_id", sa.String(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("worker_id", sa.String(), nullable=False),
        sa.Column("lease_token", sa.String(), nullable=False),
        sa.Column("lease_expires_at", sa.String(), nullable=False),
        sa.Column("fencing_token", sa.Integer(), nullable=False),
        sa.Column("heartbeat_at", sa.String(), nullable=False),
        sa.Column("retry_at", sa.String(), nullable=True),
        sa.Column("error_kind", sa.String(), nullable=True),
        sa.Column("external_execution_id", sa.String(), nullable=True),
        sa.Column("input_manifest", sa.JSON(), nullable=False),
        sa.Column("output_manifest", sa.JSON(), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=True),
    )


def _create_action_run_events() -> None:
    op.create_table(
        "action_run_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("step_key", sa.String(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=True),
        sa.Column("worker_id", sa.String(), nullable=True),
        sa.Column("fencing_token", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.UniqueConstraint("tenant_id", "run_id", "sequence", name="uq_action_run_event_sequence"),
    )
    op.create_index("ix_action_run_events_tenant_run", "action_run_events", ["tenant_id", "run_id"])


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
