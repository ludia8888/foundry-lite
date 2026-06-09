from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.engine import Engine

metadata = MetaData()


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
)

dataset_check_results = Table(
    "dataset_check_results",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("check_id", String, nullable=False),
    Column("run_id", String, nullable=False),
    Column("transaction_id", String),
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
    Column("properties", JSON, nullable=False),
    Column("base_properties", JSON, nullable=False),
    Column("edit_properties", JSON, nullable=False),
    Column("property_versions", JSON, nullable=False),
    Column("source_dataset_version_id", String),
    Column("source_hash", String),
    Column("object_version", Integer, nullable=False),
    Column("deleted", Boolean, nullable=False),
    Column("deletion_reason", String),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "object_type_id", "object_id", name="uq_object_record"),
)

object_links = Table(
    "object_links",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("link_type_id", String, nullable=False),
    Column("link_type_api_name", String, nullable=False),
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
    UniqueConstraint("tenant_id", "link_type_id", "from_object_id", "to_object_id", name="uq_link"),
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


def create_database(engine: Engine) -> None:
    metadata.create_all(engine)
