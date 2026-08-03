"""Action run and writeback tables."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Index, Integer, String, Table, UniqueConstraint, text

from foundry_lite.infrastructure.schema.base import metadata

action_runs = Table(
    "action_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("action_type_id", String, nullable=False),
    Column("action_type_api_name", String, nullable=False),
    Column("actor_user_id", String, nullable=False),
    Column("target_object_type_id", String, nullable=False),
    Column("target_object_type_api_name", String, nullable=False),
    Column("target_object_id", String, nullable=False),
    Column("expected_object_version", Integer, nullable=False),
    Column("parameters", JSON, nullable=False),
    Column("status", String, nullable=False),
    Column("idempotency_key", String, nullable=False),
    Column("request_fingerprint", String, nullable=False),
    Column("result", JSON),
    Column("error", JSON),
    # Persisted before the external write so the recovery sweep can HEAD a stranded
    # ``external_pending`` run (real external-writeback path only; NULL otherwise).
    Column("external_writeback_uri", String),
    Column("definition_version", String),
    Column("plan_hash", String),
    Column("execution_plan", JSON),
    Column("execution_mode", String, nullable=False, server_default="sync"),
    Column("workflow_run_id", String),
    Column("dispatch_status", String, nullable=False, server_default="not_required"),
    Column("dispatch_attempt_count", Integer, nullable=False, server_default=text("0")),
    Column("dispatch_error", JSON),
    Column("event_sequence", Integer, nullable=False, server_default=text("0")),
    Column("cancel_requested_at", String),
    Column("cancel_reason", String),
    Column("cancel_idempotency_key", String),
    Column("cancel_request_fingerprint", String),
    Column("started_at", String),
    Column("updated_at", String),
    Column("created_at", String, nullable=False),
    Column("completed_at", String),
    UniqueConstraint(
        "tenant_id",
        "action_type_id",
        "actor_user_id",
        "idempotency_key",
        name="uq_action_idempotency",
    ),
)

Index("ix_action_runs_tenant_created", action_runs.c.tenant_id, action_runs.c.created_at, action_runs.c.id)
Index("ix_action_runs_tenant_dispatch", action_runs.c.tenant_id, action_runs.c.dispatch_status)


action_run_steps = Table(
    "action_run_steps",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("run_id", String, nullable=False),
    Column("step_key", String, nullable=False),
    Column("step_kind", String, nullable=False),
    Column("status", String, nullable=False),
    Column("attempt_count", Integer, nullable=False, server_default=text("0")),
    Column("input_manifest", JSON, nullable=False),
    Column("output_manifest", JSON, nullable=False),
    Column("error", JSON),
    Column("started_at", String),
    Column("completed_at", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "run_id", "step_key", name="uq_action_run_step_key"),
)
Index("ix_action_run_steps_tenant_run", action_run_steps.c.tenant_id, action_run_steps.c.run_id)


action_step_attempts = Table(
    "action_step_attempts",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("step_id", String, nullable=False),
    Column("attempt_number", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("worker_id", String, nullable=False),
    Column("lease_token", String, nullable=False),
    Column("lease_expires_at", String, nullable=False),
    Column("fencing_token", Integer, nullable=False),
    Column("heartbeat_at", String, nullable=False),
    Column("retry_at", String),
    Column("error_kind", String),
    Column("external_execution_id", String),
    Column("input_manifest", JSON, nullable=False),
    Column("output_manifest", JSON, nullable=False),
    Column("error", JSON),
    Column("started_at", String, nullable=False),
    Column("completed_at", String),
    UniqueConstraint("tenant_id", "step_id", "attempt_number", name="uq_action_step_attempt_number"),
    UniqueConstraint("tenant_id", "step_id", "fencing_token", name="uq_action_step_fencing_token"),
)
Index("ix_action_step_attempts_tenant_step", action_step_attempts.c.tenant_id, action_step_attempts.c.step_id)


action_run_events = Table(
    "action_run_events",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("run_id", String, nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("event_type", String, nullable=False),
    Column("step_key", String),
    Column("attempt_number", Integer),
    Column("worker_id", String),
    Column("fencing_token", Integer),
    Column("payload", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint("tenant_id", "run_id", "sequence", name="uq_action_run_event_sequence"),
)
Index("ix_action_run_events_tenant_run", action_run_events.c.tenant_id, action_run_events.c.run_id)


action_effect_receipts = Table(
    "action_effect_receipts",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("action_run_id", String, nullable=False),
    Column("effect_id", String, nullable=False),
    Column("phase", String, nullable=False),
    Column("effect_kind", String, nullable=False),
    Column("target_ref", String, nullable=False),
    Column("status", String, nullable=False),
    Column("idempotency_key", String, nullable=False),
    Column("attempt_count", Integer, nullable=False, server_default=text("0")),
    Column("max_attempts", Integer, nullable=False),
    Column("worker_id", String),
    Column("lease_token", String),
    Column("lease_expires_at", String),
    Column("fencing_token", Integer, nullable=False, server_default=text("0")),
    Column("heartbeat_at", String),
    Column("request", JSON, nullable=False),
    Column("response", JSON),
    Column("error", JSON),
    Column("retry_at", String),
    Column("external_execution_id", String),
    Column("outbox_event_id", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("completed_at", String),
    UniqueConstraint("tenant_id", "action_run_id", "effect_id", name="uq_action_effect_receipt"),
    UniqueConstraint("tenant_id", "idempotency_key", name="uq_action_effect_idempotency"),
)
Index(
    "ix_action_effect_receipts_tenant_status",
    action_effect_receipts.c.tenant_id,
    action_effect_receipts.c.status,
    action_effect_receipts.c.retry_at,
)
Index(
    "ix_action_effect_receipts_tenant_run",
    action_effect_receipts.c.tenant_id,
    action_effect_receipts.c.action_run_id,
)


action_log_entries = Table(
    "action_log_entries",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("action_run_id", String, nullable=False),
    Column("log_object_type_api_name", String, nullable=False),
    Column("log_object_id", String, nullable=False),
    Column("action_type_id", String, nullable=False),
    Column("action_type_api_name", String, nullable=False),
    Column("definition_version", String, nullable=False),
    Column("actor_user_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("parameters", JSON, nullable=False),
    Column("result", JSON, nullable=False),
    Column("branch_id", String),
    Column("plan_hash", String),
    Column("approval_id", String),
    Column("revert_allowed", Boolean, nullable=False),
    Column("revert_status", String, nullable=False, server_default="eligible"),
    Column("reverted_by_run_id", String),
    Column("created_at", String, nullable=False),
    Column("completed_at", String, nullable=False),
    UniqueConstraint("tenant_id", "action_run_id", name="uq_action_log_run"),
    UniqueConstraint("tenant_id", "log_object_type_api_name", "log_object_id", name="uq_action_log_object"),
)
Index("ix_action_log_entries_tenant_created", action_log_entries.c.tenant_id, action_log_entries.c.created_at)


action_log_objects = Table(
    "action_log_objects",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("action_log_entry_id", String, nullable=False),
    Column("object_edit_id", String, nullable=False),
    Column("object_type_id", String, nullable=False),
    Column("object_type_api_name", String, nullable=False),
    Column("object_id", String, nullable=False),
    Column("edit_type", String, nullable=False),
    Column("ordinal", Integer, nullable=False),
    UniqueConstraint("tenant_id", "action_log_entry_id", "object_edit_id", name="uq_action_log_edit"),
)
Index("ix_action_log_objects_tenant_log", action_log_objects.c.tenant_id, action_log_objects.c.action_log_entry_id)


action_writebacks = Table(
    "action_writebacks",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("action_run_id", String, nullable=False),
    Column("mode", String, nullable=False),
    Column("connector_id", String),
    Column("request", JSON, nullable=False),
    Column("response", JSON),
    Column("status", String, nullable=False),
    Column("idempotency_key", String),
    Column("attempts", Integer, nullable=False),
    Column("created_at", String, nullable=False),
    Column("completed_at", String),
)
