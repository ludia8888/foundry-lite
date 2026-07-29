"""expand Pipeline Builder async DAG control-plane evidence

Revision ID: e1a3c5d7f9b2
Revises: d9f1a3b5c7e0
Create Date: 2026-07-29 13:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "e1a3c5d7f9b2"
down_revision: str | Sequence[str] | None = "d9f1a3b5c7e0"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _extend_pipeline_runs()
    _extend_pipeline_schedules()
    _extend_pipeline_previews()
    _extend_pipeline_node_attempts()
    _extend_pipeline_artifacts()
    _create_pipeline_run_events()
    if context.get_context().dialect.name == "postgresql":
        _enable_pipeline_run_event_rls()


def _extend_pipeline_runs() -> None:
    op.add_column(
        "pipeline_runs",
        sa.Column("dispatch_status", sa.String(), nullable=False, server_default=sa.text("'pending'")),
    )
    op.add_column(
        "pipeline_runs",
        sa.Column("dispatch_attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("pipeline_runs", sa.Column("dispatch_error", sa.JSON(), nullable=True))
    op.add_column(
        "pipeline_runs",
        sa.Column("event_sequence", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("pipeline_runs", sa.Column("cancel_requested_at", sa.String(), nullable=True))
    op.add_column("pipeline_runs", sa.Column("cancel_reason", sa.Text(), nullable=True))
    op.add_column("pipeline_runs", sa.Column("cancel_idempotency_key", sa.String(), nullable=True))
    op.add_column("pipeline_runs", sa.Column("cancel_request_fingerprint", sa.String(), nullable=True))
    op.add_column("pipeline_runs", sa.Column("schedule_id", sa.String(), nullable=True))
    op.add_column("pipeline_runs", sa.Column("schedule_slot_at", sa.String(), nullable=True))
    op.add_column("pipeline_runs", sa.Column("terminal_observed_at", sa.String(), nullable=True))
    op.create_index(
        "ix_pipeline_runs_dispatch_recovery",
        "pipeline_runs",
        ["dispatch_status", "started_at"],
    )
    op.create_index(
        "ix_pipeline_runs_schedule_terminal_observer",
        "pipeline_runs",
        ["schedule_id", "terminal_observed_at", "completed_at"],
    )


def _extend_pipeline_node_attempts() -> None:
    op.add_column(
        "pipeline_node_attempts",
        sa.Column("fencing_token", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("pipeline_node_attempts", sa.Column("heartbeat_at", sa.String(), nullable=True))
    op.add_column("pipeline_node_attempts", sa.Column("retry_at", sa.String(), nullable=True))
    op.add_column("pipeline_node_attempts", sa.Column("error_kind", sa.String(), nullable=True))
    op.add_column("pipeline_node_attempts", sa.Column("external_execution_id", sa.String(), nullable=True))


def _extend_pipeline_schedules() -> None:
    op.add_column("pipeline_schedules", sa.Column("last_terminal_run_id", sa.String(), nullable=True))
    op.add_column("pipeline_schedules", sa.Column("last_terminal_slot_at", sa.String(), nullable=True))
    op.add_column("pipeline_schedules", sa.Column("last_terminal_status", sa.String(), nullable=True))
    op.add_column("pipeline_schedules", sa.Column("last_terminal_at", sa.String(), nullable=True))


def _extend_pipeline_previews() -> None:
    op.add_column("pipeline_preview_runs", sa.Column("workflow_run_id", sa.String(), nullable=True))
    op.add_column(
        "pipeline_preview_runs",
        sa.Column("dispatch_status", sa.String(), nullable=False, server_default=sa.text("'pending'")),
    )
    op.add_column(
        "pipeline_preview_runs",
        sa.Column("dispatch_attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("pipeline_preview_runs", sa.Column("dispatch_error", sa.JSON(), nullable=True))
    op.create_index(
        "ix_pipeline_preview_dispatch_recovery",
        "pipeline_preview_runs",
        ["dispatch_status", "created_at"],
    )


def _extend_pipeline_artifacts() -> None:
    op.add_column("pipeline_run_artifacts", sa.Column("attempt_id", sa.String(), nullable=True))
    op.add_column("pipeline_run_artifacts", sa.Column("fencing_token", sa.Integer(), nullable=True))


def _create_pipeline_run_events() -> None:
    op.create_table(
        "pipeline_run_events",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("node_id", sa.String(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=True),
        sa.Column("worker_id", sa.String(), nullable=True),
        sa.Column("fencing_token", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "sequence",
            name="uq_pipeline_run_event_sequence",
        ),
    )
    op.create_index(
        "ix_pipeline_run_events_stream",
        "pipeline_run_events",
        ["tenant_id", "run_id", "sequence"],
    )


def _enable_pipeline_run_event_rls() -> None:
    op.execute(sa.text("ALTER TABLE pipeline_run_events ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE pipeline_run_events FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            "CREATE POLICY pipeline_run_events_tenant_isolation ON pipeline_run_events "
            "USING (tenant_id = current_setting('foundry_lite.tenant_id', true)) "
            "WITH CHECK (tenant_id = current_setting('foundry_lite.tenant_id', true))"
        )
    )


def downgrade() -> None:
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")
