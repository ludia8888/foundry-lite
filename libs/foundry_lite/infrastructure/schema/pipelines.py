"""Pipeline Builder graph, governance, run, and schedule tables."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Index, Integer, String, Table, Text, UniqueConstraint, text

from foundry_lite.infrastructure.schema.base import metadata

pipeline_branches = Table(
    "pipeline_branches",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("pipeline_id", String, nullable=False),
    Column("name", String, nullable=False),
    Column("status", String, nullable=False),
    Column("base_version_id", String),
    Column("base_graph", JSON, nullable=False),
    Column("graph", JSON, nullable=False),
    Column("graph_fingerprint", String, nullable=False),
    Column("graph_schema_version", Integer, nullable=False, server_default=text("1")),
    Column("created_by", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("rebased_at", String),
    Column("proposal_id", String),
    Column("merged_version_id", String),
)


pipeline_proposals = Table(
    "pipeline_proposals",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("pipeline_id", String, nullable=False),
    Column("branch_id", String, nullable=False),
    Column("title", String, nullable=False),
    Column("description", Text),
    Column("status", String, nullable=False),
    Column("graph", JSON, nullable=False),
    Column("graph_fingerprint", String, nullable=False),
    Column("graph_schema_version", Integer, nullable=False, server_default=text("1")),
    Column("assigned_to", String),
    Column("decision", String),
    Column("decision_comment", Text),
    Column("decided_at", String),
    Column("created_by", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
)


pipeline_versions = Table(
    "pipeline_versions",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("pipeline_id", String, nullable=False),
    Column("version_number", Integer, nullable=False),
    Column("graph", JSON, nullable=False),
    Column("graph_fingerprint", String, nullable=False),
    Column("graph_schema_version", Integer, nullable=False, server_default=text("1")),
    Column("execution_plan", JSON),
    Column("plan_fingerprint", String),
    Column("compiler_version", String),
    Column("proposal_id", String, nullable=False),
    Column("created_by", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("deployed_at", String),
    UniqueConstraint("tenant_id", "pipeline_id", "version_number", name="uq_pipeline_version_number"),
)


pipeline_runs = Table(
    "pipeline_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("pipeline_id", String, nullable=False),
    Column("version_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("idempotency_key", String),
    Column("request_fingerprint", String),
    Column("plan_fingerprint", String),
    Column("workflow_run_id", String),
    Column("dispatch_status", String, nullable=False, server_default=text("'pending'")),
    Column("dispatch_attempt_count", Integer, nullable=False, server_default=text("0")),
    Column("dispatch_error", JSON),
    Column("event_sequence", Integer, nullable=False, server_default=text("0")),
    Column("execution_lease_token", String),
    Column("execution_lease_expires_at", String),
    Column("execution_heartbeat_at", String),
    Column("cancel_requested_at", String),
    Column("cancel_reason", Text),
    Column("cancel_idempotency_key", String),
    Column("cancel_request_fingerprint", String),
    Column("schedule_id", String),
    Column("schedule_slot_at", String),
    Column("terminal_observed_at", String),
    Column("parameters", JSON),
    Column("target_node_ids", JSON),
    Column("outputs", JSON, nullable=False, server_default=text("'[]'")),
    Column("output_dataset_ref", String),
    Column("output_version_id", String),
    Column("timeline", JSON, nullable=False),
    Column("error", JSON),
    Column("created_by", String, nullable=False),
    Column("started_at", String, nullable=False),
    Column("completed_at", String),
)

Index(
    "ix_pipeline_runs_tenant_idempotency",
    pipeline_runs.c.tenant_id,
    pipeline_runs.c.idempotency_key,
    unique=True,
)

Index(
    "ix_pipeline_runs_dispatch_recovery",
    pipeline_runs.c.dispatch_status,
    pipeline_runs.c.started_at,
)

Index(
    "ix_pipeline_runs_schedule_terminal_observer",
    pipeline_runs.c.schedule_id,
    pipeline_runs.c.terminal_observed_at,
    pipeline_runs.c.completed_at,
)


pipeline_schedules = Table(
    "pipeline_schedules",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("pipeline_id", String, nullable=False),
    Column("version_id", String, nullable=False),
    Column("schedule", JSON, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("status", String, nullable=False, server_default=text("'active'")),
    Column("updated_by", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("last_tick_at", String),
    Column("last_slot_at", String),
    Column("trigger_type", String),
    Column("timezone", String),
    Column("next_due_at", String),
    Column("runtime_config_updated_at", String),
    Column("lease_owner", String),
    Column("lease_token", String),
    Column("lease_expires_at", String),
    Column("fencing_token", Integer, nullable=False, server_default=text("0")),
    Column("failure_count", Integer, nullable=False, server_default=text("0")),
    Column("paused_reason", Text),
    Column("last_failure_at", String),
    Column("last_error", JSON),
    Column("last_terminal_run_id", String),
    Column("last_terminal_slot_at", String),
    Column("last_terminal_status", String),
    Column("last_terminal_at", String),
    UniqueConstraint("tenant_id", "pipeline_id", name="uq_pipeline_schedule_pipeline"),
)

Index(
    "ix_pipeline_schedules_due",
    pipeline_schedules.c.tenant_id,
    pipeline_schedules.c.enabled,
    pipeline_schedules.c.next_due_at,
)


pipeline_schedule_operations = Table(
    "pipeline_schedule_operations",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("pipeline_id", String, nullable=False),
    Column("operation", String, nullable=False),
    Column("idempotency_key", String, nullable=False),
    Column("request_fingerprint", String, nullable=False),
    Column("result", JSON),
    Column("created_by", String, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint("tenant_id", "idempotency_key", name="uq_pipeline_schedule_operation_idempotency"),
)


pipeline_test_results = Table(
    "pipeline_test_results",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("pipeline_id", String, nullable=False),
    Column("branch_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("result", JSON, nullable=False),
    Column("created_by", String, nullable=False),
    Column("created_at", String, nullable=False),
)
