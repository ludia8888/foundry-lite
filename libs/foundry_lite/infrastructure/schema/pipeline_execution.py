"""Pipeline v2 resources, deployments, execution attempts, artifacts, and previews."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Index, Integer, String, Table, Text, UniqueConstraint, text

from foundry_lite.infrastructure.schema.base import metadata

pipelines = Table(
    "pipelines",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("resource_id", String),
    Column("project_id", String),
    Column("folder_id", String),
    Column("name", String, nullable=False),
    Column("description", Text),
    Column("status", String, nullable=False),
    Column("default_branch_id", String),
    Column("created_by", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "name", name="uq_pipeline_resource_name"),
)


pipeline_deployments = Table(
    "pipeline_deployments",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("pipeline_id", String, nullable=False),
    Column("version_id", String, nullable=False),
    Column("deployment_number", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("execution_plan", JSON, nullable=False),
    Column("plan_fingerprint", String, nullable=False),
    Column("compiler_version", String, nullable=False),
    Column("processor_pins", JSON, nullable=False),
    Column("model_pins", JSON, nullable=False),
    Column("function_pins", JSON, nullable=False),
    Column("compute_profile", JSON, nullable=False),
    Column("idempotency_key", String, nullable=False),
    Column("request_fingerprint", String, nullable=False),
    Column("promoted_by", String),
    Column("promoted_at", String),
    Column("rolled_back_from_id", String),
    Column("created_by", String, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint("tenant_id", "pipeline_id", "deployment_number", name="uq_pipeline_deployment_number"),
    UniqueConstraint("tenant_id", "idempotency_key", name="uq_pipeline_deployment_idempotency"),
)


pipeline_node_runs = Table(
    "pipeline_node_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("run_id", String, nullable=False),
    Column("node_id", String, nullable=False),
    Column("descriptor_id", String, nullable=False),
    Column("spec_version", String, nullable=False),
    Column("status", String, nullable=False),
    Column("attempt_count", Integer, nullable=False, server_default=text("0")),
    Column("input_artifacts", JSON, nullable=False),
    Column("output_artifacts", JSON, nullable=False),
    Column("error", JSON),
    Column("started_at", String),
    Column("completed_at", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "run_id", "node_id", name="uq_pipeline_node_run"),
)

Index(
    "ix_pipeline_node_runs_run",
    pipeline_node_runs.c.tenant_id,
    pipeline_node_runs.c.run_id,
    pipeline_node_runs.c.status,
)


pipeline_node_attempts = Table(
    "pipeline_node_attempts",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("node_run_id", String, nullable=False),
    Column("attempt_number", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("executor_profile", String, nullable=False),
    Column("lease_owner", String),
    Column("lease_token", String),
    Column("lease_expires_at", String),
    Column("input_manifest", JSON, nullable=False),
    Column("output_manifest", JSON, nullable=False),
    Column("error", JSON),
    Column("started_at", String, nullable=False),
    Column("completed_at", String),
    UniqueConstraint("tenant_id", "node_run_id", "attempt_number", name="uq_pipeline_node_attempt"),
)


pipeline_run_artifacts = Table(
    "pipeline_run_artifacts",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("run_id", String, nullable=False),
    Column("node_run_id", String),
    Column("node_id", String, nullable=False),
    Column("port_id", String, nullable=False),
    Column("artifact_kind", String, nullable=False),
    Column("plane", String, nullable=False),
    Column("artifact_ref", JSON, nullable=False),
    Column("manifest", JSON, nullable=False),
    Column("content_fingerprint", String, nullable=False),
    Column("security_envelope", JSON, nullable=False),
    Column("status", String, nullable=False),
    Column("is_serving", Boolean, nullable=False, server_default=text("false")),
    Column("idempotency_key", String, nullable=False),
    Column("committed_at", String),
    Column("created_at", String, nullable=False),
    UniqueConstraint("tenant_id", "idempotency_key", name="uq_pipeline_artifact_idempotency"),
)

Index(
    "ix_pipeline_run_artifacts_run",
    pipeline_run_artifacts.c.tenant_id,
    pipeline_run_artifacts.c.run_id,
)


pipeline_preview_runs = Table(
    "pipeline_preview_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("pipeline_id", String, nullable=False),
    Column("branch_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("graph", JSON, nullable=False),
    Column("graph_fingerprint", String, nullable=False),
    Column("target_node_id", String),
    Column("limits", JSON, nullable=False),
    Column("outputs", JSON, nullable=False),
    Column("artifacts", JSON, nullable=False),
    Column("idempotency_key", String, nullable=False),
    Column("request_fingerprint", String, nullable=False),
    Column("is_commit_forbidden", Boolean, nullable=False, server_default=text("true")),
    Column("execution_context", JSON, nullable=False, server_default=text("'{}'")),
    Column("execution_lease_token", String),
    Column("execution_lease_expires_at", String),
    Column("execution_heartbeat_at", String),
    Column("cancel_requested_at", String),
    Column("error", JSON),
    Column("created_by", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("started_at", String),
    Column("completed_at", String),
    UniqueConstraint("tenant_id", "idempotency_key", name="uq_pipeline_preview_idempotency"),
)

Index(
    "ix_pipeline_preview_branch",
    pipeline_preview_runs.c.tenant_id,
    pipeline_preview_runs.c.branch_id,
    pipeline_preview_runs.c.created_at,
)

Index(
    "ix_pipeline_preview_recovery",
    pipeline_preview_runs.c.status,
    pipeline_preview_runs.c.execution_lease_expires_at,
    pipeline_preview_runs.c.created_at,
)


pipeline_semantic_row_cache = Table(
    "pipeline_semantic_row_cache",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("cache_key", String, nullable=False),
    Column("request_fingerprint", String, nullable=False),
    Column("pipeline_id", String, nullable=False),
    Column("scope_kind", String, nullable=False),
    Column("scope_id", String, nullable=False),
    Column("node_id", String, nullable=False),
    Column("descriptor_id", String, nullable=False),
    Column("spec_version", String, nullable=False),
    Column("cache_generation", Integer, nullable=False),
    Column("resource_security_policy_fingerprint", String, nullable=False),
    Column("model_alias", String, nullable=False),
    Column("environment", String, nullable=False),
    Column("resolved_model_id", String, nullable=False),
    Column("resolved_model_revision", String, nullable=False),
    Column("provider", String, nullable=False),
    Column("prompt_version_id", String, nullable=False),
    Column("prompt_fingerprint", String, nullable=False),
    Column("input_fingerprint", String, nullable=False),
    Column("media_fingerprint", String, nullable=False),
    Column("output_schema_fingerprint", String, nullable=False),
    Column("config_fingerprint", String, nullable=False),
    Column("output_value", JSON, nullable=False),
    Column("model_evidence", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint("tenant_id", "cache_key", name="uq_pipeline_semantic_row_cache_key"),
)
