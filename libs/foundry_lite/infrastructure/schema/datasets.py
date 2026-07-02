"""Dataset catalog, transaction, version, file, and quality contract tables."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Integer, String, Table, Text, UniqueConstraint

from foundry_lite.infrastructure.schema.base import metadata

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


webhook_event_keys = Table(
    "webhook_event_keys",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("dataset_id", String, nullable=False),
    Column("connector_name", String, nullable=False),
    Column("resource_name", String, nullable=False),
    Column("event_id", String, nullable=False),
    Column("transaction_id", String, nullable=False),
    Column("committed_version_id", String, nullable=False),
    Column("payload_hash", String, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint(
        "tenant_id",
        "dataset_id",
        "connector_name",
        "resource_name",
        "event_id",
        name="uq_webhook_event_key",
    ),
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
    Column("data_contract_version_id", String),
    Column("run_id", String, nullable=False),
    Column("transaction_id", String),
    Column("checked_manifest_hash", String, nullable=False),
    Column("validated_against_schema_version_id", String, nullable=False),
    Column("validated_against_schema_version", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("details", JSON, nullable=False),
    Column("created_at", String, nullable=False),
)


dataset_quality_contract_versions = Table(
    "dataset_quality_contract_versions",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("dataset_id", String, nullable=False),
    Column("contract_key", String, nullable=False),
    Column("version", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("owner_user_id", String),
    Column("description", Text),
    Column("checks_snapshot", JSON, nullable=False),
    Column("schema_version_id", String),
    Column("schema_version", Integer),
    Column("created_at", String, nullable=False),
    Column("activated_at", String),
    UniqueConstraint("tenant_id", "dataset_id", "contract_key", "version", name="uq_dataset_quality_contract_version"),
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
