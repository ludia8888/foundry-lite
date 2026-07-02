"""AIP model, session, execution, retrieval, eval, and release tables."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Float, Integer, String, Table, Text, UniqueConstraint

from foundry_lite.infrastructure.schema.base import metadata

ai_model_providers = Table(
    "ai_model_providers",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("tenant_scope", String, nullable=False),
    Column("provider_type", String, nullable=False),
    Column("profile_name", String, nullable=False),
    Column("region", String, nullable=False),
    # The secret NAME the SecretProvider resolves by; the raw value never lives in the DB or trace.
    Column("secret_ref", String, nullable=False),
    # ZDR / no-retrain egress assurance the destination promises (§9.3).
    Column("retention_policy", String, nullable=False),
    Column("training_policy", String, nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint("tenant_id", "profile_name", name="uq_ai_model_provider_profile"),
)


ai_models = Table(
    "ai_models",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("tenant_scope", String, nullable=False),
    Column("provider_id", String, nullable=False),
    # The provider-native model id (the resolved provider comes from the joined provider row).
    Column("provider_model_id", String, nullable=False),
    Column("revision", String, nullable=False),
    Column("lifecycle", String, nullable=False),
    Column("capabilities_json", JSON, nullable=False),
    Column("context_limit", Integer, nullable=False),
    Column("output_limit", Integer, nullable=False),
    Column("pricing_json", JSON, nullable=False),
    # Export-control marking the destination accepts; the gateway gates classification against it.
    Column("allowed_classifications", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint("tenant_id", "id", name="uq_ai_model_tenant_id"),
)


ai_model_aliases = Table(
    "ai_model_aliases",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("tenant_scope", String, nullable=False),
    Column("alias", String, nullable=False),
    Column("environment", String, nullable=False),
    Column("model_id", String, nullable=False),
    # NULL version = floating latest; a value pins the alias to that model revision (prod-recommended).
    Column("version", String),
    Column("status", String, nullable=False),
    # Eval evidence link (no FK — ai_eval_runs is a later phase).
    Column("eval_run_id", String),
    Column("effective_at", String),
    Column("retired_at", String),
    UniqueConstraint("tenant_id", "alias", "environment", name="uq_ai_model_alias"),
)


ai_sessions = Table(
    "ai_sessions",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("agent_version_id", String, nullable=False),
    Column("actor_user_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("last_activity_at", String, nullable=False),
)


ai_session_state_versions = Table(
    "ai_session_state_versions",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("session_id", String, nullable=False),
    Column("version", Integer, nullable=False),
    Column("state_json", JSON, nullable=False),
    Column("state_hash", String, nullable=False),
    Column("created_by_run_id", String),
    Column("created_at", String, nullable=False),
)


ai_messages = Table(
    "ai_messages",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("session_id", String, nullable=False),
    Column("role", String, nullable=False),
    Column("client_message_id", String, nullable=False),
    Column("content_ref", String, nullable=False),
    Column("content_hash", String, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint("tenant_id", "session_id", "client_message_id", name="uq_ai_message_client_id"),
)


ai_execution_runs = Table(
    "ai_execution_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("session_id", String, nullable=False),
    Column("agent_version_id", String, nullable=False),
    Column("actor_user_id", String, nullable=False),
    Column("request_id", String, nullable=False),
    Column("trace_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("ontology_version_id", String, nullable=False),
    Column("model_alias_version", String, nullable=False),
    Column("resolved_model_id", String, nullable=False),
    Column("resolved_model_revision", String, nullable=False),
    Column("prompt_version_id", String, nullable=False),
    Column("compiled_prompt_hash", String, nullable=False),
    Column("tool_manifest_hash", String, nullable=False),
    Column("context_manifest_hash", String, nullable=False),
    Column("state_snapshot_hash", String, nullable=False),
    Column("policy_snapshot_hash", String, nullable=False),
    Column("budget_json", JSON, nullable=False),
    Column("usage_json", JSON),
    Column("error_json", JSON),
    Column("started_at", String, nullable=False),
    Column("completed_at", String),
)


ai_execution_events = Table(
    "ai_execution_events",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("ai_run_id", String, nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("event_type", String, nullable=False),
    Column("payload_ref", String, nullable=False),
    Column("payload_hash", String, nullable=False),
    Column("redacted_preview", String, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint("ai_run_id", "sequence", name="uq_ai_execution_event_sequence"),
)


ai_model_calls = Table(
    "ai_model_calls",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("ai_run_id", String, nullable=False),
    Column("attempt", Integer, nullable=False),
    Column("provider", String, nullable=False),
    Column("model_revision", String, nullable=False),
    Column("request_hash", String, nullable=False),
    Column("response_hash", String, nullable=False),
    Column("provider_request_id", String, nullable=False),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("latency_ms", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("error_json", JSON),
)


ai_context_items = Table(
    "ai_context_items",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("ai_run_id", String, nullable=False),
    Column("context_id", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("source_resource_type", String, nullable=False),
    Column("source_resource_id", String, nullable=False),
    Column("source_version", String, nullable=False),
    Column("content_hash", String, nullable=False),
    Column("retrieval_method", String, nullable=False),
    Column("relevance_score", Float, nullable=False),
    Column("security_partition", String, nullable=False),
    Column("token_estimate", Integer, nullable=False),
    Column("selected", Boolean, nullable=False),
    Column("omission_reason", String),
)


ai_tool_calls = Table(
    "ai_tool_calls",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("ai_run_id", String, nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("tool_id", String, nullable=False),
    Column("tool_version", String, nullable=False),
    Column("arguments_hash", String, nullable=False),
    Column("effect", String, nullable=False),
    Column("authorization_decision", String, nullable=False),
    Column("confirmation_policy", String, nullable=False),
    Column("status", String, nullable=False),
    Column("result_hash", String, nullable=False),
    Column("linked_action_run_id", String),
    Column("started_at", String, nullable=False),
    Column("completed_at", String),
    Column("error_json", JSON),
)


ai_citations = Table(
    "ai_citations",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("ai_run_id", String, nullable=False),
    Column("message_id", String, nullable=False),
    Column("context_item_id", String, nullable=False),
    Column("claim_span", JSON, nullable=False),
    Column("citation_order", Integer, nullable=False),
    Column("rendered_ref", String, nullable=False),
)


ai_usage_ledger = Table(
    "ai_usage_ledger",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("ai_run_id", String, nullable=False),
    Column("provider", String, nullable=False),
    Column("model_revision", String, nullable=False),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("estimated_cost", Float, nullable=False),
    Column("currency", String, nullable=False),
    Column("recorded_at", String, nullable=False),
)


ai_prompt_artifacts = Table(
    "ai_prompt_artifacts",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("ai_run_id", String, nullable=False),
    Column("artifact_kind", String, nullable=False),
    Column("artifact_ref", Text, nullable=False),
    Column("content_hash", String, nullable=False),
    Column("artifact_hash", String, nullable=False),
    Column("byte_size", Integer, nullable=False),
    Column("encryption_key_ref", String, nullable=False),
    Column("encryption_algorithm", String, nullable=False),
    Column("retention_until", String, nullable=False),
    Column("legal_hold", Boolean, nullable=False),
    Column("erasure_request_id", String),
    Column("export_marking", String, nullable=False),
    Column("created_at", String, nullable=False),
)


ai_eval_suites = Table(
    "ai_eval_suites",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("suite_api_name", String, nullable=False),
    Column("version", String, nullable=False),
    Column("description", String, nullable=False),
    Column("axes_json", JSON, nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint("tenant_id", "suite_api_name", "version", name="uq_ai_eval_suite_version"),
)


ai_eval_cases = Table(
    "ai_eval_cases",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("suite_id", String, nullable=False),
    Column("case_api_name", String, nullable=False),
    Column("axis", String, nullable=False),
    Column("input_json", JSON, nullable=False),
    Column("expected_json", JSON, nullable=False),
    Column("rubric_json", JSON, nullable=False),
    Column("tags_json", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint("tenant_id", "suite_id", "case_api_name", name="uq_ai_eval_case_name"),
)


ai_eval_runs = Table(
    "ai_eval_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("suite_id", String, nullable=False),
    Column("agent_version_id", String, nullable=False),
    Column("candidate_release_channel", String, nullable=False),
    Column("status", String, nullable=False),
    Column("min_score", Float, nullable=False),
    Column("passed", Boolean, nullable=False),
    Column("summary_json", JSON, nullable=False),
    Column("started_at", String, nullable=False),
    Column("completed_at", String),
)


ai_eval_results = Table(
    "ai_eval_results",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("eval_run_id", String, nullable=False),
    Column("case_id", String, nullable=False),
    Column("sample_index", Integer, nullable=False),
    Column("axis", String, nullable=False),
    Column("score", Float, nullable=False),
    Column("passed", Boolean, nullable=False),
    Column("evaluator", String, nullable=False),
    Column("input_hash", String, nullable=False),
    Column("expected_hash", String, nullable=False),
    Column("actual_hash", String, nullable=False),
    Column("result_json", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint("eval_run_id", "case_id", "sample_index", "axis", name="uq_ai_eval_result_sample"),
)


ai_agent_releases = Table(
    "ai_agent_releases",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("agent_version_id", String, nullable=False),
    Column("release_channel", String, nullable=False),
    Column("eval_run_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("policy_version", String, nullable=False),
    Column("promoted_by", String, nullable=False),
    Column("promoted_at", String, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint("tenant_id", "agent_version_id", "release_channel", name="uq_ai_agent_release_channel"),
)
