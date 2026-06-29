from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.engine import Connection, Engine

metadata = MetaData()
POSTGRES_TENANT_SETTING = "foundry_lite.tenant_id"


tenants = Table(
    "tenants",
    metadata,
    Column("id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("created_at", String, nullable=False),
)

users = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("email", String, nullable=False),
    Column("roles", JSON, nullable=False),
    Column("created_at", String, nullable=False),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("actor_user_id", String),
    Column("event_type", String, nullable=False),
    Column("resource_type", String, nullable=False),
    Column("resource_id", String),
    Column("action", String),
    Column("decision", String),
    Column("policy_decision", JSON),
    Column("before_ref", JSON),
    Column("after_ref", JSON),
    Column("correlation_id", String),
    Column("request_id", String),
    Column("metadata", JSON, nullable=False),
    Column("created_at", String, nullable=False),
)

datasets = Table(
    "datasets",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("namespace", String, nullable=False),
    Column("name", String, nullable=False),
    Column("description", Text),
    Column("storage_kind", String, nullable=False),
    Column("storage_uri", Text),
    Column("owner_team", String),
    Column("classification", String),
    Column("status", String, nullable=False),
    Column("primary_key", JSON, nullable=False),
    Column("partition_spec", JSON, nullable=False),
    Column("sort_order", JSON, nullable=False),
    Column("target_file_size_bytes", Integer),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "namespace", "name", name="uq_dataset_ref"),
)

dataset_schemas = Table(
    "dataset_schemas",
    metadata,
    Column("id", String, primary_key=True),
    Column("dataset_id", String, nullable=False),
    Column("version", Integer, nullable=False),
    Column("schema_json", JSON, nullable=False),
    Column("schema_hash", String, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint("dataset_id", "version", name="uq_dataset_schema_version"),
    UniqueConstraint("dataset_id", "schema_hash", name="uq_dataset_schema_hash"),
)

dataset_transactions = Table(
    "dataset_transactions",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("dataset_id", String, nullable=False),
    Column("branch", String, nullable=False),
    Column("tx_type", String, nullable=False),
    Column("status", String, nullable=False),
    Column("base_version_id", String),
    Column("committed_version_id", String),
    Column("schema_version", Integer),
    Column("created_by", String),
    Column("created_at", String, nullable=False),
    Column("committed_at", String),
    Column("metadata", JSON, nullable=False),
)

dataset_versions = Table(
    "dataset_versions",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("dataset_id", String, nullable=False),
    Column("branch", String, nullable=False),
    Column("version_number", Integer, nullable=False),
    Column("transaction_id", String, nullable=False),
    Column("schema_version", Integer, nullable=False),
    Column("manifest_uri", Text, nullable=False),
    Column("row_count", Integer, nullable=False),
    Column("byte_size", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("superseded_by_version_id", String),
    Column("created_at", String, nullable=False),
    UniqueConstraint("dataset_id", "branch", "version_number", name="uq_dataset_version_number"),
)

dataset_files = Table(
    "dataset_files",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("dataset_version_id", String, nullable=False),
    Column("uri", Text, nullable=False),
    Column("format", String, nullable=False),
    Column("row_count", Integer, nullable=False),
    Column("byte_size", Integer, nullable=False),
    Column("content_hash", String, nullable=False),
    Column("partition_values", JSON, nullable=False),
)

dataset_checks = Table(
    "dataset_checks",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("dataset_id", String, nullable=False),
    Column("name", String, nullable=False),
    Column("check_type", String, nullable=False),
    Column("config", JSON, nullable=False),
    Column("severity", String, nullable=False),
    Column("enabled", Boolean, nullable=False),
    UniqueConstraint("tenant_id", "dataset_id", "name", name="uq_dataset_check_name"),
)

dataset_check_results = Table(
    "dataset_check_results",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("check_id", String, nullable=False),
    Column("run_id", String, nullable=False),
    Column("transaction_id", String),
    Column("checked_manifest_hash", String, nullable=False),
    Column("validated_against_schema_version_id", String, nullable=False),
    Column("validated_against_schema_version", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("details", JSON, nullable=False),
    Column("created_at", String, nullable=False),
)

sync_runs = Table(
    "sync_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("sync_name", String, nullable=False),
    Column("source_type", String, nullable=False),
    Column("output_dataset_id", String, nullable=False),
    Column("transaction_id", String),
    Column("committed_version_id", String),
    Column("status", String, nullable=False),
    Column("error", JSON),
    Column("created_at", String, nullable=False),
    Column("completed_at", String),
)

transforms = Table(
    "transforms",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("api_name", String, nullable=False),
    Column("language", String, nullable=False),
    Column("entrypoint", Text, nullable=False),
    Column("mode", String, nullable=False),
    Column("inputs", JSON, nullable=False),
    Column("output_dataset_ref", String, nullable=False),
    Column("checks", JSON, nullable=False),
    UniqueConstraint("tenant_id", "api_name", name="uq_transform_api_name"),
)

transform_runs = Table(
    "transform_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("transform_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("input_versions", JSON, nullable=False),
    Column("definition_snapshot", JSON),
    Column("output_version_id", String),
    Column("transaction_id", String),
    Column("error", JSON),
    Column("created_at", String, nullable=False),
    Column("completed_at", String),
)

lineage_edges = Table(
    "lineage_edges",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("from_resource_type", String, nullable=False),
    Column("from_resource_id", String, nullable=False),
    Column("to_resource_type", String, nullable=False),
    Column("to_resource_id", String, nullable=False),
    Column("relation", String, nullable=False),
    Column("created_by_run_id", String),
    Column("created_at", String, nullable=False),
)

runtime_run_relations = Table(
    "runtime_run_relations",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("source_run_type", String, nullable=False),
    Column("source_run_id", String, nullable=False),
    Column("target_run_type", String, nullable=False),
    Column("target_run_id", String, nullable=False),
    Column("relation", String, nullable=False),
    Column("resource_type", String, nullable=False),
    Column("resource_id", String, nullable=False),
    Column("metadata", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint(
        "tenant_id",
        "source_run_type",
        "source_run_id",
        "target_run_type",
        "target_run_id",
        "relation",
        "resource_type",
        "resource_id",
        name="uq_runtime_run_relation",
    ),
)

ontology_versions = Table(
    "ontology_versions",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("version_number", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("created_by", String),
    Column("created_at", String, nullable=False),
    Column("activated_at", String),
)
Index(
    "uq_ontology_active_per_tenant",
    ontology_versions.c.tenant_id,
    unique=True,
    sqlite_where=ontology_versions.c.status == "active",
    postgresql_where=ontology_versions.c.status == "active",
)

object_types = Table(
    "object_types",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("ontology_version_id", String, nullable=False),
    Column("api_name", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("description", Text),
    Column("primary_key_property", String, nullable=False),
    Column("backing", JSON, nullable=False),
    Column("config", JSON, nullable=False),
    UniqueConstraint("tenant_id", "ontology_version_id", "api_name", name="uq_object_type_api"),
)

property_types = Table(
    "property_types",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("object_type_id", String, nullable=False),
    Column("api_name", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("data_type", String, nullable=False),
    Column("nullable", Boolean, nullable=False),
    Column("indexed", Boolean, nullable=False),
    Column("searchable", Boolean, nullable=False),
    Column("editable", Boolean, nullable=False),
    Column("classification", String),
    Column("source", String, nullable=False),
    Column("column_name", String),
    Column("edit_policy", String, nullable=False),
    Column("derivation", JSON),
    UniqueConstraint("object_type_id", "api_name", name="uq_property_api"),
)

link_types = Table(
    "link_types",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("ontology_version_id", String, nullable=False),
    Column("api_name", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("from_object_type_id", String, nullable=False),
    Column("from_api_name", String, nullable=False),
    Column("to_object_type_id", String, nullable=False),
    Column("to_api_name", String, nullable=False),
    Column("cardinality", String, nullable=False),
    Column("backing", JSON, nullable=False),
    UniqueConstraint("tenant_id", "ontology_version_id", "api_name", name="uq_link_type_api"),
)

action_types = Table(
    "action_types",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("ontology_version_id", String, nullable=False),
    Column("api_name", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("target_object_type_id", String, nullable=False),
    Column("target_api_name", String, nullable=False),
    Column("parameter_schema", JSON, nullable=False),
    Column("definition", JSON, nullable=False),
    Column("enabled", Boolean, nullable=False),
    UniqueConstraint("tenant_id", "ontology_version_id", "api_name", name="uq_action_type_api"),
)

object_records = Table(
    "object_records",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("object_type_id", String, nullable=False),
    Column("object_type_api_name", String, nullable=False),
    Column("object_id", String, nullable=False),
    Column("index_version", String, nullable=False, default="active"),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("properties", JSON, nullable=False),
    Column("base_properties", JSON, nullable=False),
    Column("edit_properties", JSON, nullable=False),
    Column("property_versions", JSON, nullable=False),
    Column("source_dataset_version_id", String),
    Column("source_hash", String),
    Column("object_version", Integer, nullable=False),
    Column("object_change_sequence", Integer),
    Column("deleted", Boolean, nullable=False),
    Column("deletion_reason", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "object_type_id", "object_id", "index_version", name="uq_object_record"),
)

object_change_counters = Table(
    "object_change_counters",
    metadata,
    Column("tenant_id", String, primary_key=True),
    Column("last_sequence", Integer, nullable=False),
)

object_record_versions = Table(
    "object_record_versions",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("object_record_id", String, nullable=False),
    Column("object_type_id", String, nullable=False),
    Column("object_type_api_name", String, nullable=False),
    Column("object_id", String, nullable=False),
    Column("index_version", String, nullable=False),
    Column("is_active", Boolean, nullable=False),
    Column("properties", JSON, nullable=False),
    Column("base_properties", JSON, nullable=False),
    Column("edit_properties", JSON, nullable=False),
    Column("property_versions", JSON, nullable=False),
    Column("source_dataset_version_id", String),
    Column("source_hash", String),
    Column("object_version", Integer, nullable=False),
    Column("object_change_sequence", Integer, nullable=False),
    Column("deleted", Boolean, nullable=False),
    Column("deletion_reason", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint(
        "tenant_id",
        "object_type_id",
        "object_id",
        "index_version",
        "object_change_sequence",
        name="uq_object_record_version_sequence",
    ),
)

object_links = Table(
    "object_links",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("link_type_id", String, nullable=False),
    Column("link_type_api_name", String, nullable=False),
    Column("index_version", String, nullable=False, default="active"),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("from_object_type_id", String, nullable=False),
    Column("from_api_name", String, nullable=False),
    Column("from_object_id", String, nullable=False),
    Column("to_object_type_id", String, nullable=False),
    Column("to_api_name", String, nullable=False),
    Column("to_object_id", String, nullable=False),
    Column("properties", JSON, nullable=False),
    Column("source_dataset_version_id", String),
    Column("link_version", Integer, nullable=False),
    Column("deleted", Boolean, nullable=False),
    Column("deletion_reason", String),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "link_type_id", "from_object_id", "to_object_id", "index_version", name="uq_link"),
)

object_index_versions = Table(
    "object_index_versions",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("object_type_id", String, nullable=False),
    Column("active_index_version", String, nullable=False, default="active"),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "object_type_id", name="uq_object_index_version_active"),
)

object_edits = Table(
    "object_edits",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("action_run_id", String),
    Column("object_type_id", String, nullable=False),
    Column("object_type_api_name", String, nullable=False),
    Column("object_id", String, nullable=False),
    Column("edit_type", String, nullable=False),
    Column("patch", JSON, nullable=False),
    Column("previous_values", JSON, nullable=False),
    Column("actor_user_id", String),
    Column("idempotency_key", String),
    Column("created_at", String, nullable=False),
)

object_conflicts = Table(
    "object_conflicts",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("object_type_id", String, nullable=False),
    Column("object_id", String, nullable=False),
    Column("property_api_name", String, nullable=False),
    Column("source_value", JSON),
    Column("edit_value", JSON),
    Column("source_dataset_version_id", String),
    Column("edit_id", String),
    Column("status", String, nullable=False),
    Column("created_at", String, nullable=False),
)

insight_reviews = Table(
    "insight_reviews",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("claim_id", String, nullable=False),
    Column("claim_text", Text, nullable=False),
    Column("status", String, nullable=False),
    Column("priority", String, nullable=False),
    Column("assignee_user_id", String),
    Column("evidence_object_ids", JSON, nullable=False),
    Column("evidence_refs", JSON, nullable=False),
    Column("action_proposal", JSON),
    Column("proposal_type", String),
    Column("proposal_fingerprint", String),
    Column("originating_ai_run_id", String),
    Column("originating_tool_call_id", String),
    Column("expires_at", String),
    Column("execution_status", String),
    Column("approved_action_run_id", String),
    Column("approval_policy_version", String),
    Column("created_by_user_id", String, nullable=False),
    Column("created_idempotency_key", String, nullable=False),
    Column("assignment_idempotency_key", String),
    Column("decision", JSON),
    Column("decision_idempotency_key", String),
    Column("review_metadata", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "created_idempotency_key", name="uq_insight_review_create_idempotency"),
    UniqueConstraint("tenant_id", "assignment_idempotency_key", name="uq_insight_review_assignment_idempotency"),
    UniqueConstraint("tenant_id", "decision_idempotency_key", name="uq_insight_review_decision_idempotency"),
)

object_sets = Table(
    "object_sets",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("name", String, nullable=False),
    Column("object_type_id", String, nullable=False),
    Column("set_type", String, nullable=False),
    Column("definition", JSON, nullable=False),
    Column("visibility", String, nullable=False),
    Column("owner_user_id", String),
    Column("expires_at", String),
    Column("created_at", String, nullable=False),
)

index_runs = Table(
    "index_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("object_type_id", String, nullable=False),
    Column("object_type_api_name", String, nullable=False),
    Column("trigger_type", String, nullable=False),
    Column("source_ref", JSON, nullable=False),
    Column("status", String, nullable=False),
    Column("cursor", JSON),
    Column("rows_read", Integer, nullable=False),
    Column("objects_upserted", Integer, nullable=False),
    Column("objects_deleted", Integer, nullable=False),
    Column("links_upserted", Integer, nullable=False),
    Column("error", JSON),
    Column("started_at", String),
    Column("completed_at", String),
    Column("created_at", String, nullable=False),
)

outbox_events = Table(
    "outbox_events",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("event_type", String, nullable=False),
    Column("aggregate_type", String, nullable=False),
    Column("aggregate_id", String, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("status", String, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("idempotency_key", String),
    Column("correlation_id", String),
    Column("created_at", String, nullable=False),
    Column("published_at", String),
    UniqueConstraint("tenant_id", "event_type", "idempotency_key", name="uq_outbox_idempotency"),
)

dead_letter_events = Table(
    "dead_letter_events",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("source_event_id", String),
    Column("event_type", String, nullable=False),
    Column("payload", JSON, nullable=False),
    Column("error", JSON, nullable=False),
    Column("failed_at", String, nullable=False),
    Column("retry_after", String),
)

dead_letter_records = Table(
    "dead_letter_records",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("source_event_id", String, nullable=False),
    Column("source_dataset_version_id", String),
    Column("source_run_id", String),
    Column("payload", JSON, nullable=False),
    Column("payload_hash", String, nullable=False),
    Column("schema_version", Integer),
    Column("transform_version", String),
    Column("error_kind", String, nullable=False),
    Column("error_message", Text, nullable=False),
    Column("event_time", String),
    Column("ingested_at", String, nullable=False),
    Column("first_failed_at", String, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("replay_status", String, nullable=False),
    Column("replay_run_id", String),
    Column("replay_idempotency_key", String),
    Column("replay_requested_at", String),
    Column("replay_lease_token", String),
    Column("replay_started_at", String),
    Column("replay_lease_expires_at", String),
    Column("discarded_at", String),
    Column("backfill_plan", JSON),
    Column("is_closed_partition_affected", Boolean, nullable=False),
    Column("metadata", JSON, nullable=False),
    UniqueConstraint("tenant_id", "source_event_id", "payload_hash", name="uq_dead_letter_record_source_payload"),
)

connector_connections = Table(
    "connector_connections",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("connector_name", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("base_url", Text, nullable=False),
    Column("auth", JSON, nullable=False),
    Column("rate_limit_per_minute", Integer),
    Column("allow_private_network", Boolean, nullable=False),
    Column("status", String, nullable=False),
    Column("config_fingerprint", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "connector_name", name="uq_connector_connection_name"),
)

connector_resources = Table(
    "connector_resources",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("connector_name", String, nullable=False),
    Column("resource_name", String, nullable=False),
    Column("dataset_ref", String, nullable=False),
    Column("resource_path", Text, nullable=False),
    Column("pagination", JSON, nullable=False),
    Column("schema_columns", JSON, nullable=False),
    Column("primary_key", JSON, nullable=False),
    Column("config_fingerprint", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "connector_name", "resource_name", name="uq_connector_resource_name"),
)

source_connections = Table(
    "source_connections",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("source_name", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("target_dataset_ref", String),
    Column("target_media_set_id", String),
    Column("status", String, nullable=False),
    Column("config_summary", JSON, nullable=False),
    Column("config_fingerprint", String, nullable=False),
    Column("last_run_id", String),
    Column("last_workflow_run_id", String),
    Column("last_commit_ref", JSON),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "source_name", name="uq_source_connection_name"),
)

source_credentials = Table(
    "source_credentials",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("credential_name", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("auth_scheme", String, nullable=False),
    Column("secret_name", String, nullable=False),
    Column("secret_version", String, nullable=False),
    Column("status", String, nullable=False),
    Column("config_summary", JSON, nullable=False),
    Column("config_fingerprint", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "credential_name", name="uq_source_credential_name"),
)

source_agents = Table(
    "source_agents",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("agent_id", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("mode", String, nullable=False),
    Column("status", String, nullable=False),
    Column("capabilities", JSON, nullable=False),
    Column("network_summary", JSON, nullable=False),
    Column("last_heartbeat_at", String),
    Column("config_fingerprint", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "agent_id", name="uq_source_agent_id"),
)

source_network_policies = Table(
    "source_network_policies",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("policy_name", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("mode", String, nullable=False),
    Column("agent_id", String),
    Column("allowed_hosts", JSON, nullable=False),
    Column("status", String, nullable=False),
    Column("config_fingerprint", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "policy_name", name="uq_source_network_policy_name"),
)

source_exploration_runs = Table(
    "source_exploration_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("source_name", String, nullable=False),
    Column("source_type", String, nullable=False),
    Column("request", JSON, nullable=False),
    Column("status", String, nullable=False),
    Column("result_summary", JSON, nullable=False),
    Column("error", JSON),
    Column("operations_path", String, nullable=False),
    Column("created_at", String, nullable=False),
)

source_syncs = Table(
    "source_syncs",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("sync_name", String, nullable=False),
    Column("source_name", String, nullable=False),
    Column("display_name", String, nullable=False),
    Column("source_type", String, nullable=False),
    Column("capability", String, nullable=False),
    Column("target_dataset_ref", String),
    Column("target_media_set_id", String),
    Column("mode", String, nullable=False),
    Column("schedule", JSON, nullable=False),
    Column("config_summary", JSON, nullable=False),
    Column("config_fingerprint", String, nullable=False),
    Column("status", String, nullable=False),
    Column("last_run_id", String),
    Column("last_workflow_run_id", String),
    Column("checkpoint", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "sync_name", name="uq_source_sync_name"),
)

source_sync_runs = Table(
    "source_sync_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("sync_name", String, nullable=False),
    Column("source_name", String, nullable=False),
    Column("source_type", String, nullable=False),
    Column("capability", String, nullable=False),
    Column("workflow_run_id", String),
    Column("dataset_version_id", String),
    Column("status", String, nullable=False),
    Column("trigger_type", String, nullable=False),
    Column("idempotency_key", String, nullable=False),
    Column("batch_limit", Integer),
    Column("checkpoint_start", JSON, nullable=False),
    Column("checkpoint_end", JSON, nullable=False),
    Column("result_summary", JSON, nullable=False),
    Column("error", JSON),
    Column("operations_path", String, nullable=False),
    Column("started_at", String, nullable=False),
    Column("completed_at", String),
    Column("created_at", String, nullable=False),
    UniqueConstraint("tenant_id", "sync_name", "idempotency_key", name="uq_source_sync_run_idempotency"),
)

workflow_runs = Table(
    "workflow_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("workflow_name", String, nullable=False),
    Column("workflow_profile", String, nullable=False),
    Column("status", String, nullable=False),
    Column("idempotency_key", String, nullable=False),
    Column("request_fingerprint", String, nullable=False),
    Column("input", JSON, nullable=False),
    Column("output", JSON, nullable=False),
    Column("error", JSON),
    Column("dataset_id", String),
    Column("audit_event_id", String),
    Column("attempts", Integer, nullable=False),
    Column("created_at", String, nullable=False),
    Column("started_at", String),
    Column("completed_at", String),
    UniqueConstraint("tenant_id", "workflow_name", "idempotency_key", name="uq_workflow_run_idempotency"),
)

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

materializations = Table(
    "materializations",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("api_name", String, nullable=False),
    Column("materialization_type", String, nullable=False),
    Column("source_ref", JSON, nullable=False),
    Column("target_ref", JSON, nullable=False),
    Column("trigger_config", JSON, nullable=False),
    Column("enabled", Boolean, nullable=False),
    UniqueConstraint("tenant_id", "api_name", name="uq_materialization_api_name"),
)

materialization_runs = Table(
    "materialization_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("materialization_id", String, nullable=False),
    Column("api_name", String, nullable=False),
    Column("status", String, nullable=False),
    Column("source_cursor", JSON),
    Column("object_store_watermark", JSON),
    Column("consistency_level", String, nullable=False),
    Column("target_dataset_version_id", String),
    Column("row_count", Integer),
    Column("error", JSON),
    Column("created_at", String, nullable=False),
    Column("completed_at", String),
)


erasure_requests = Table(
    "erasure_requests",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("subject_kind", String, nullable=False),
    # Only the secret-backed HMAC token is persisted; the raw subject value never lands here.
    Column("subject_hash", String, nullable=False),
    Column("subject_token_key_id", String, nullable=False),
    Column("requested_by", String, nullable=False),
    Column("idempotency_key", String, nullable=False),
    Column("reason", String, nullable=False),
    Column("status", String, nullable=False),
    Column("manifest_id", String),
    Column("evidence", JSON, nullable=False),
    Column("requested_at", String, nullable=False),
    Column("executed_at", String),
    UniqueConstraint("tenant_id", "idempotency_key", name="uq_erasure_requests_tenant_idempotency"),
)


erasure_certificates = Table(
    "erasure_certificates",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("request_id", String, nullable=False),
    Column("manifest_id", String, nullable=False),
    Column("certificate", JSON, nullable=False),
    Column("certified_at", String, nullable=False),
    UniqueConstraint("tenant_id", "request_id", name="uq_erasure_certificates_tenant_request"),
)


erasure_redaction_executions = Table(
    "erasure_redaction_executions",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("request_id", String, nullable=False),
    # The deferred resource the maintenance lane physically erased (e.g. one dataset row).
    Column("resource_type", String, nullable=False),
    Column("resource_id", String, nullable=False),
    Column("surface", String, nullable=False),
    Column("status", String, nullable=False),
    # The successor dataset version produced by the rewrite, when one was committed.
    Column("new_version_id", String),
    Column("executed_at", String, nullable=False),
    UniqueConstraint(
        "tenant_id",
        "request_id",
        "resource_type",
        "resource_id",
        name="uq_erasure_redaction_execution_resource",
    ),
)


media_sets = Table(
    "media_sets",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("namespace", String, nullable=False),
    Column("name", String, nullable=False),
    Column("schema_type", String, nullable=False),
    Column("primary_format", String, nullable=False),
    Column("allowed_input_formats", JSON, nullable=False),
    Column("transaction_policy", String, nullable=False),
    Column("storage_profile", String, nullable=False),
    Column("processing_profile", String, nullable=False),
    Column("classification", String, nullable=False),
    Column("retention_policy_id", String),
    # A virtual media set reads from an external source without copying bytes (M9); its
    # versions must pin an etag or be marked mutable_external_reference (source_ref).
    Column("is_virtual", Boolean, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "namespace", "name", name="uq_media_set_ref"),
)


media_transactions = Table(
    "media_transactions",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("media_set_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("mode", String, nullable=False),
    Column("idempotency_key", String, nullable=False),
    Column("request_fingerprint", String, nullable=False),
    Column("opened_at", String, nullable=False),
    Column("committed_at", String),
    Column("aborted_at", String),
    Column("error", JSON),
    Column("trace", JSON),
    UniqueConstraint("tenant_id", "media_set_id", "idempotency_key", name="uq_media_transaction_idempotency"),
)


media_items = Table(
    "media_items",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("media_set_id", String, nullable=False),
    Column("logical_path", String, nullable=False),
    # Moving head pointer to the latest version; not a historical reference.
    Column("head_version_id", String),
    Column("is_deleted", Boolean, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "media_set_id", "logical_path", name="uq_media_item_path"),
)


media_item_versions = Table(
    "media_item_versions",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("media_item_id", String, nullable=False),
    Column("media_transaction_id", String, nullable=False),
    Column("version_number", Integer, nullable=False),
    Column("blob_key", String, nullable=False),
    Column("content_hash", String, nullable=False),
    Column("byte_size", Integer, nullable=False),
    Column("supplied_mime_type", String, nullable=False),
    Column("sniffed_mime_type", String, nullable=False),
    Column("schema_type", String, nullable=False),
    Column("format", String, nullable=False),
    Column("probe_metadata", JSON, nullable=False),
    # Security envelope copied from the media set; derivatives must never be weaker.
    Column("security_envelope", JSON, nullable=False),
    Column("source_ref", JSON),
    Column("status", String, nullable=False),
    # Retention mark (L7): when a retention policy marks this version as a sweep candidate.
    # A marked version is purged only after the grace period AND only if it is neither
    # reachable by a MediaReference binding nor under legal hold (fail-safe).
    Column("retention_marked_at", String),
    Column("legal_hold", Boolean, nullable=False, server_default=text("false")),
    Column("created_at", String, nullable=False),
    Column("committed_at", String),
    UniqueConstraint("media_item_id", "version_number", name="uq_media_item_version_number"),
    UniqueConstraint("tenant_id", "content_hash", "byte_size", "blob_key", name="uq_media_version_blob"),
)

# A processing output pinned to (source version + processor spec). The unique key is the
# media-processing idempotency anchor (doc §4.4): the same version + spec + model + params
# resolves to the existing derivative, never a duplicate. model_version/params_hash are
# stored as "" (not NULL) when absent so the uniqueness holds across backends.
media_derivatives = Table(
    "media_derivatives",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("source_media_item_version_id", String, nullable=False),
    Column("derivative_kind", String, nullable=False),
    Column("processor_spec_hash", String, nullable=False),
    Column("processor_name", String, nullable=False),
    Column("processor_version", String, nullable=False),
    Column("model_name", String),
    Column("model_version", String, nullable=False),
    Column("params_hash", String, nullable=False),
    Column("blob_key", String),
    Column("content_hash", String),
    Column("byte_size", Integer),
    Column("mime_type", String),
    # Security envelope copied from the source version; a derivative is never weaker.
    Column("security_envelope", JSON, nullable=False),
    Column("status", String, nullable=False),
    Column("error", JSON),
    Column("created_at", String, nullable=False),
    Column("committed_at", String),
    UniqueConstraint(
        "source_media_item_version_id",
        "derivative_kind",
        "processor_spec_hash",
        "model_version",
        "params_hash",
        name="uq_media_derivative_spec",
    ),
)

# Normalized content extracted from a derivative (page/chunk/segment). Pins the exact
# source version and the producing derivative; never re-resolves the logical-path head
# (doc §4.5). text_hash makes a citation verifiable against the source.
content_units = Table(
    "content_units",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("source_media_item_version_id", String, nullable=False),
    Column("derivative_id", String, nullable=False),
    Column("unit_kind", String, nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("page_number", Integer),
    Column("start_ms", Integer),
    Column("end_ms", Integer),
    Column("bbox", JSON),
    Column("speaker", String),
    Column("language", String),
    # L11: a vision unit's CLIP frame embedding (list[float]); null for text units, which embed
    # at index time. Persisting it makes the visual index rebuildable from committed truth.
    Column("embedding", JSON),
    Column("text", String, nullable=False),
    Column("text_hash", String, nullable=False),
    Column("chunk_spec_hash", String, nullable=False),
    Column("security_envelope", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint(
        "source_media_item_version_id",
        "unit_kind",
        "ordinal",
        "chunk_spec_hash",
        name="uq_content_unit_ordinal",
    ),
)

# An Ontology object property of type media_reference, bound to one immutable media version
# (doc §1.6/§6.4). One binding per (object, property); the value pins media_item_version_id,
# so a later head overwrite never changes what a previously read reference resolves to. The
# security envelope is copied for read-time masking (object-query masking, §12.1).
# On-demand access-pattern cache (thumbnail/preview/waveform), SEPARATE from media_derivatives:
# a derivative is committed truth produced inside a transaction; a cache is TTL/evictable and
# rebuildable from the committed source, NEVER serving truth (doc §M9, invariant
# access_pattern_cache_is_not_truth). source_content_hash pins the source so a stale cache is
# detectable and re-rendered; only the access-pattern service reads this table.
media_access_caches = Table(
    "media_access_caches",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("source_media_item_version_id", String, nullable=False),
    Column("access_pattern", String, nullable=False),
    Column("access_pattern_spec_hash", String, nullable=False),
    Column("persistence_policy", String, nullable=False),
    Column("source_content_hash", String, nullable=False),
    Column("blob_key", String, nullable=False),
    Column("content_hash", String, nullable=False),
    Column("byte_size", Integer, nullable=False),
    Column("mime_type", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("expires_at", String),
    UniqueConstraint(
        "tenant_id",
        "source_media_item_version_id",
        "access_pattern",
        "access_pattern_spec_hash",
        name="uq_media_access_cache_key",
    ),
)


media_reference_bindings = Table(
    "media_reference_bindings",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("holder_type", String, nullable=False),
    Column("holder_id", String, nullable=False),
    Column("property_name", String, nullable=False),
    Column("media_set_id", String, nullable=False),
    Column("media_item_id", String, nullable=False),
    Column("media_item_version_id", String, nullable=False),
    Column("logical_path", String, nullable=False),
    Column("content_hash", String, nullable=False),
    Column("security_envelope", JSON, nullable=False),
    Column("idempotency_key", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "holder_type", "holder_id", "property_name", name="uq_media_reference_binding"),
)


# Operations/run surface for media processing (L0). One row per processing attempt:
# RUNNING -> SUCCEEDED/FAILED, carrying typed failure evidence so an operator can see a
# failed attempt (the operator-evidence proof class). This row is orchestration/operations
# evidence, NEVER serving truth — the COMMITTED derivative + DB remain the only success
# signal (invariant workflow_status_does_not_replace_domain_commit); a FAILED/RUNNING run
# never makes an uncommitted derivative visible. Mirrors index_runs / transform_runs.
media_processing_runs = Table(
    "media_processing_runs",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("source_media_item_version_id", String, nullable=False),
    Column("processor_name", String, nullable=False),
    Column("derivative_kind", String, nullable=False),
    Column("processing_spec_hash", String, nullable=False),
    Column("status", String, nullable=False),
    Column("media_derivative_id", String),
    Column("failure_kind", String),
    Column("failure_reason", String),
    Column("started_at", String, nullable=False),
    Column("finished_at", String),
    Column("created_at", String, nullable=False),
)


# AIP-lite P0b — governed Model Gateway catalog + alias indirection (§10.1).
# `tenant_id` is the RLS-discovery column (see `tenant_rls_tables`); the canonical `tenant_scope`
# column carries the §10.1 scope value (kept consistent with `tenant_id` by the repository).
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

# AIP-lite P0c — AI run/event ledger (§10.2). These rows are the durable
# "what happened" trail for a model-assisted turn. Raw prompt/tool payloads stay
# outside the general DB; rows store refs, hashes, redacted previews, and counts.
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

# AIP-lite P0v — encrypted prompt artifact metadata. The raw prompt bytes live
# in a separate encrypted store; this table is only the tenant-scoped receipt.
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

# AIP-lite P0k — eval evidence and release promotion guard (§8.12/§15).
# These rows are the local proof that an agent/model/prompt candidate passed a
# deterministic eval gate before being promoted to an operational release channel.
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


def tenant_rls_tables() -> tuple[Table, ...]:
    return tuple(table for table in metadata.sorted_tables if "tenant_id" in table.c)


def create_database(engine: Engine) -> None:
    metadata.create_all(engine)
    apply_postgres_rls(engine)


def apply_postgres_rls(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        for table in tenant_rls_tables():
            _apply_tenant_rls_policy(conn, table)
        _apply_dataset_schema_rls_policy(conn)


def set_postgres_tenant_context(conn: Connection, tenant_id: str, *, is_local: bool = True) -> None:
    if conn.dialect.name != "postgresql":
        return
    conn.execute(
        text(f"SELECT set_config('{POSTGRES_TENANT_SETTING}', :tenant_id, :local)"),
        {"tenant_id": tenant_id, "local": is_local},
    )


def _apply_tenant_rls_policy(conn: Connection, table: Table) -> None:
    table_name = _table_identifier(conn, table)
    policy_name = _identifier(conn, f"{table.name}_tenant_isolation")
    condition = f"tenant_id = current_setting('{POSTGRES_TENANT_SETTING}', true)"
    conn.execute(text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
    conn.execute(text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
    conn.execute(text(f"DROP POLICY IF EXISTS {policy_name} ON {table_name}"))
    conn.execute(text(f"CREATE POLICY {policy_name} ON {table_name} USING ({condition}) WITH CHECK ({condition})"))


def _apply_dataset_schema_rls_policy(conn: Connection) -> None:
    table_name = _table_identifier(conn, dataset_schemas)
    datasets_name = _table_identifier(conn, datasets)
    policy_name = _identifier(conn, "dataset_schemas_tenant_isolation")
    # Policy DDL must interpolate identifiers; both names come from static
    # SQLAlchemy metadata and are quoted by the active dialect preparer.
    condition = (
        f"EXISTS (SELECT 1 FROM {datasets_name} AS d "  # nosec B608
        f"WHERE d.id = {table_name}.dataset_id "
        f"AND d.tenant_id = current_setting('{POSTGRES_TENANT_SETTING}', true))"
    )
    conn.execute(text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
    conn.execute(text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
    conn.execute(text(f"DROP POLICY IF EXISTS {policy_name} ON {table_name}"))
    conn.execute(text(f"CREATE POLICY {policy_name} ON {table_name} USING ({condition}) WITH CHECK ({condition})"))


def _table_identifier(conn: Connection, table: Table) -> str:
    return conn.dialect.identifier_preparer.format_table(table)


def _identifier(conn: Connection, name: str) -> str:
    return conn.dialect.identifier_preparer.quote(name)
