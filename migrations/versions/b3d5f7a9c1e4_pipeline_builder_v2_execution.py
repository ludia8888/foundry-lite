"""expand Pipeline Builder v2 execution, preview, deployment, and artifact evidence

Revision ID: b3d5f7a9c1e4
Revises: f0a2c4e6b8d0
Create Date: 2026-07-16 14:00:00.000000
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import context, op

revision: str = "b3d5f7a9c1e4"
down_revision: str | Sequence[str] | None = "f0a2c4e6b8d0"
migration_phase: str = "expand"
release_compatibility: str = "old_and_new_app"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _extend_pipeline_graph_rows()
    _extend_pipeline_runs()
    _extend_pipeline_schedules()
    _create_pipeline_schedule_operations()
    _extend_content_and_lineage()
    _create_pipeline_resources()
    _create_pipeline_deployments()
    _create_pipeline_node_runs()
    _create_pipeline_node_attempts()
    _create_pipeline_run_artifacts()
    _create_pipeline_preview_runs()
    if context.get_context().dialect.name == "postgresql":
        for table in _tenant_tables():
            _enable_tenant_rls(table)


def downgrade() -> None:
    raise NotImplementedError("S55 forward-fix policy blocks destructive production downgrades.")


def _extend_pipeline_graph_rows() -> None:
    version_default = sa.text("1")
    for table in ("pipeline_branches", "pipeline_proposals", "pipeline_versions"):
        op.add_column(
            table, sa.Column("graph_schema_version", sa.Integer(), nullable=False, server_default=version_default)
        )
    op.add_column("pipeline_versions", sa.Column("execution_plan", sa.JSON(), nullable=True))
    op.add_column("pipeline_versions", sa.Column("plan_fingerprint", sa.String(), nullable=True))
    op.add_column("pipeline_versions", sa.Column("compiler_version", sa.String(), nullable=True))


def _extend_pipeline_runs() -> None:
    for name in (
        "idempotency_key",
        "request_fingerprint",
        "plan_fingerprint",
        "workflow_run_id",
        "execution_lease_token",
        "execution_lease_expires_at",
        "execution_heartbeat_at",
    ):
        op.add_column("pipeline_runs", sa.Column(name, sa.String(), nullable=True))
    op.add_column("pipeline_runs", sa.Column("parameters", sa.JSON(), nullable=True))
    op.add_column("pipeline_runs", sa.Column("target_node_ids", sa.JSON(), nullable=True))
    op.add_column(
        "pipeline_runs",
        sa.Column("outputs", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.create_index(
        "ix_pipeline_runs_tenant_idempotency",
        "pipeline_runs",
        ["tenant_id", "idempotency_key"],
        unique=True,
    )


def _extend_pipeline_schedules() -> None:
    for name in ("trigger_type", "timezone", "next_due_at", "lease_owner", "lease_token", "lease_expires_at"):
        op.add_column("pipeline_schedules", sa.Column(name, sa.String(), nullable=True))
    op.add_column(
        "pipeline_schedules",
        sa.Column("fencing_token", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "pipeline_schedules",
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "pipeline_schedules",
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'active'")),
    )
    op.add_column("pipeline_schedules", sa.Column("last_slot_at", sa.String(), nullable=True))
    op.add_column("pipeline_schedules", sa.Column("paused_reason", sa.Text(), nullable=True))
    op.add_column("pipeline_schedules", sa.Column("last_failure_at", sa.String(), nullable=True))
    op.add_column("pipeline_schedules", sa.Column("last_error", sa.JSON(), nullable=True))
    op.create_index("ix_pipeline_schedules_due", "pipeline_schedules", ["tenant_id", "enabled", "next_due_at"])
    _backfill_pipeline_schedules()


def _backfill_pipeline_schedules() -> None:
    schedules = sa.table(
        "pipeline_schedules",
        sa.column("id", sa.String()),
        sa.column("schedule", sa.JSON()),
        sa.column("enabled", sa.Boolean()),
        sa.column("updated_at", sa.String()),
        sa.column("trigger_type", sa.String()),
        sa.column("timezone", sa.String()),
        sa.column("next_due_at", sa.String()),
        sa.column("status", sa.String()),
        sa.column("paused_reason", sa.Text()),
    )
    connection = op.get_bind()
    migration_started_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    rows = (
        connection.execute(
            sa.select(
                schedules.c.id,
                schedules.c.schedule,
                schedules.c.enabled,
                schedules.c.updated_at,
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        schedule = row["schedule"] if isinstance(row["schedule"], dict) else {}
        is_enabled = bool(row["enabled"])
        connection.execute(
            schedules.update()
            .where(schedules.c.id == row["id"])
            .values(
                trigger_type=_legacy_trigger_type(schedule),
                timezone=str(schedule.get("timezone") or "UTC"),
                next_due_at=migration_started_at if is_enabled else None,
                status="active" if is_enabled else "paused",
                paused_reason=None if is_enabled else "disabled_before_pipeline_v2_upgrade",
            )
        )


def _legacy_trigger_type(schedule: dict[str, object]) -> str:
    declared = schedule.get("triggerType") or schedule.get("type") or schedule.get("kind")
    if isinstance(declared, str) and declared.strip():
        return declared.strip().lower()
    if schedule.get("cronExpression") or schedule.get("cron"):
        return "cron"
    return "interval"


def _create_pipeline_schedule_operations() -> None:
    op.create_table(
        "pipeline_schedule_operations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("pipeline_id", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("request_fingerprint", sa.String(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_pipeline_schedule_operation_idempotency",
        ),
    )


def _extend_content_and_lineage() -> None:
    op.add_column("content_units", sa.Column("parent_content_unit_id", sa.String(), nullable=True))
    op.add_column("content_units", sa.Column("source_locator", sa.JSON(), nullable=True))
    op.add_column("content_units", sa.Column("structure", sa.JSON(), nullable=True))
    op.add_column("content_units", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column(
        "lineage_edges",
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )


def _create_pipeline_resources() -> None:
    op.create_table(
        "pipelines",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("resource_id", sa.String(), nullable=True),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("folder_id", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("default_branch_id", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_pipeline_resource_name"),
    )


def _create_pipeline_deployments() -> None:
    op.create_table(
        "pipeline_deployments",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("pipeline_id", sa.String(), nullable=False),
        sa.Column("version_id", sa.String(), nullable=False),
        sa.Column("deployment_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("execution_plan", sa.JSON(), nullable=False),
        sa.Column("plan_fingerprint", sa.String(), nullable=False),
        sa.Column("compiler_version", sa.String(), nullable=False),
        sa.Column("processor_pins", sa.JSON(), nullable=False),
        sa.Column("model_pins", sa.JSON(), nullable=False),
        sa.Column("function_pins", sa.JSON(), nullable=False),
        sa.Column("compute_profile", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("request_fingerprint", sa.String(), nullable=False),
        sa.Column("promoted_by", sa.String(), nullable=True),
        sa.Column("promoted_at", sa.String(), nullable=True),
        sa.Column("rolled_back_from_id", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "pipeline_id", "deployment_number", name="uq_pipeline_deployment_number"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_pipeline_deployment_idempotency"),
    )


def _create_pipeline_node_runs() -> None:
    op.create_table(
        "pipeline_node_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("descriptor_id", sa.String(), nullable=False),
        sa.Column("spec_version", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("input_artifacts", sa.JSON(), nullable=False),
        sa.Column("output_artifacts", sa.JSON(), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.String(), nullable=True),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "run_id", "node_id", name="uq_pipeline_node_run"),
    )
    op.create_index("ix_pipeline_node_runs_run", "pipeline_node_runs", ["tenant_id", "run_id", "status"])


def _create_pipeline_node_attempts() -> None:
    op.create_table(
        "pipeline_node_attempts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("node_run_id", sa.String(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("executor_profile", sa.String(), nullable=False),
        sa.Column("lease_owner", sa.String(), nullable=True),
        sa.Column("lease_token", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.String(), nullable=True),
        sa.Column("input_manifest", sa.JSON(), nullable=False),
        sa.Column("output_manifest", sa.JSON(), nullable=False),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.String(), nullable=False),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "node_run_id", "attempt_number", name="uq_pipeline_node_attempt"),
    )


def _create_pipeline_run_artifacts() -> None:
    op.create_table(
        "pipeline_run_artifacts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("node_run_id", sa.String(), nullable=True),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("port_id", sa.String(), nullable=False),
        sa.Column("artifact_kind", sa.String(), nullable=False),
        sa.Column("plane", sa.String(), nullable=False),
        sa.Column("artifact_ref", sa.JSON(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("content_fingerprint", sa.String(), nullable=False),
        sa.Column("security_envelope", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("is_serving", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("committed_at", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_pipeline_artifact_idempotency"),
    )
    op.create_index("ix_pipeline_run_artifacts_run", "pipeline_run_artifacts", ["tenant_id", "run_id"])


def _create_pipeline_preview_runs() -> None:
    op.create_table(
        "pipeline_preview_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("pipeline_id", sa.String(), nullable=False),
        sa.Column("branch_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("graph", sa.JSON(), nullable=False),
        sa.Column("graph_fingerprint", sa.String(), nullable=False),
        sa.Column("target_node_id", sa.String(), nullable=True),
        sa.Column("limits", sa.JSON(), nullable=False),
        sa.Column("outputs", sa.JSON(), nullable=False),
        sa.Column("artifacts", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("request_fingerprint", sa.String(), nullable=False),
        sa.Column("is_commit_forbidden", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("cancel_requested_at", sa.String(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("started_at", sa.String(), nullable=True),
        sa.Column("completed_at", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_pipeline_preview_idempotency"),
    )
    op.create_index("ix_pipeline_preview_branch", "pipeline_preview_runs", ["tenant_id", "branch_id", "created_at"])


def _tenant_tables() -> tuple[str, ...]:
    return (
        "pipelines",
        "pipeline_deployments",
        "pipeline_node_runs",
        "pipeline_node_attempts",
        "pipeline_run_artifacts",
        "pipeline_preview_runs",
        "pipeline_schedule_operations",
    )


def _enable_tenant_rls(table: str) -> None:
    condition = "tenant_id = current_setting('foundry_lite.tenant_id', true)"
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.execute(f"CREATE POLICY {table}_tenant_isolation ON {table} USING ({condition}) WITH CHECK ({condition})")
