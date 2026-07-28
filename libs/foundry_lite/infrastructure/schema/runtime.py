"""Lineage, runtime relations, observability, outbox, DLQ, workflow, and index run tables."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Integer, String, Table, Text, UniqueConstraint, text

from foundry_lite.infrastructure.schema.base import metadata

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
    Column("metadata", JSON, nullable=False, server_default=text("'{}'")),
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


observability_incidents = Table(
    "observability_incidents",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("detector_id", String, nullable=False),
    Column("detector_type", String, nullable=False),
    Column("config_version", String, nullable=False),
    Column("status", String, nullable=False),
    Column("severity", String, nullable=False),
    Column("owner", String, nullable=False),
    Column("message", Text, nullable=False),
    Column("dedupe_key", String, nullable=False),
    Column("first_observed_at", String, nullable=False),
    Column("last_observed_at", String, nullable=False),
    Column("occurrence_count", Integer, nullable=False),
    Column("evidence", JSON, nullable=False),
    Column("evidence_links", JSON, nullable=False),
    Column("threshold", JSON, nullable=False),
    Column("acknowledged_at", String),
    Column("acknowledged_by", String),
    Column("resolved_at", String),
    Column("resolved_by", String),
    Column("resolution_reason", Text),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "dedupe_key", name="uq_observability_incident_dedupe"),
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
    Column("claimed_at", String),
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
