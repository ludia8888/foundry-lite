"""Connector and source onboarding/sync tables."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Index, Integer, String, Table, Text, UniqueConstraint

from foundry_lite.infrastructure.schema.base import metadata

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


source_connection_tests = Table(
    "source_connection_tests",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("source_name", String, nullable=False),
    Column("source_type", String, nullable=False),
    Column("status", String, nullable=False),
    Column("config_fingerprint", String, nullable=False),
    Column("idempotency_key", String, nullable=False),
    Column("checks", JSON, nullable=False),
    Column("error", JSON),
    Column("operations_path", String, nullable=False),
    Column("started_at", String, nullable=False),
    Column("completed_at", String),
    Column("created_at", String, nullable=False),
    UniqueConstraint("tenant_id", "source_name", "idempotency_key", name="uq_source_connection_test_key"),
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


# A virtual table stores the POINTER only — never the external rows. The pinned ``schema``
# column is what downstream object types and pipeline nodes were built against, so a source
# that drifts away from it is reported rather than silently followed.
virtual_tables = Table(
    "virtual_tables",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("name", String, nullable=False),
    Column("parent_rid", String, nullable=False),
    Column("connection_rid", String, nullable=False),
    # Union-shaped per Palantir's Create Virtual Table contract: a SQL source identifies a
    # table by schema+name, object storage by path+format. Flattening loses that distinction.
    Column("config", JSON, nullable=False),
    Column("pinned_schema", JSON, nullable=False),
    Column("markings", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint("tenant_id", "parent_rid", "name", name="uq_virtual_table_name"),
)
Index(
    "ix_virtual_tables_tenant_connection",
    virtual_tables.c.tenant_id,
    virtual_tables.c.connection_rid,
)
