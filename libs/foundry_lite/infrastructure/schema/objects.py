"""Object record, version, link, edit, conflict, insight review, and object set tables."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Index, Integer, String, Table, Text, UniqueConstraint

from foundry_lite.infrastructure.schema.base import metadata, postgres_json_document_type

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
    Column("properties", postgres_json_document_type(), nullable=False),
    Column("base_properties", postgres_json_document_type(), nullable=False),
    Column("edit_properties", postgres_json_document_type(), nullable=False),
    Column("property_versions", postgres_json_document_type(), nullable=False),
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

Index(
    "ix_object_records_serving_lookup",
    object_records.c.tenant_id,
    object_records.c.object_type_api_name,
    object_records.c.is_active,
    object_records.c.deleted,
    object_records.c.object_id,
)
Index(
    "ix_object_records_type_version",
    object_records.c.tenant_id,
    object_records.c.object_type_id,
    object_records.c.index_version,
    object_records.c.object_id,
)
Index(
    "ix_object_records_change_sequence",
    object_records.c.tenant_id,
    object_records.c.object_type_api_name,
    object_records.c.is_active,
    object_records.c.object_change_sequence,
)
Index(
    "ix_object_records_properties_gin",
    object_records.c.properties,
    postgresql_using="gin",
    postgresql_ops={"properties": "jsonb_path_ops"},
).ddl_if(dialect="postgresql")


object_change_counters = Table(
    "object_change_counters",
    metadata,
    Column("tenant_id", String, primary_key=True),
    Column("last_sequence", Integer, nullable=False),
)


# One row per currently indexed primary key (replaced on each refresh), not one
# per dataset version: the changelog only needs "what is indexed right now" to
# diff the next version, and a bounded table keeps the diff read cheap.
object_index_row_hashes = Table(
    "object_index_row_hashes",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("object_type_id", String, nullable=False),
    Column("object_id", String, nullable=False),
    Column("row_hash", String, nullable=False),
    Column("dataset_version_id", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "object_type_id", "object_id", name="uq_object_index_row_hash"),
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
    Column("properties", postgres_json_document_type(), nullable=False),
    Column("base_properties", postgres_json_document_type(), nullable=False),
    Column("edit_properties", postgres_json_document_type(), nullable=False),
    Column("property_versions", postgres_json_document_type(), nullable=False),
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

Index(
    "ix_object_record_versions_change",
    object_record_versions.c.tenant_id,
    object_record_versions.c.object_type_api_name,
    object_record_versions.c.index_version,
    object_record_versions.c.object_change_sequence,
    object_record_versions.c.object_id,
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
    Column("properties", postgres_json_document_type(), nullable=False),
    Column("source_dataset_version_id", String),
    Column("link_version", Integer, nullable=False),
    Column("deleted", Boolean, nullable=False),
    Column("deletion_reason", String),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "link_type_id", "from_object_id", "to_object_id", "index_version", name="uq_link"),
)

Index(
    "ix_object_links_from_active",
    object_links.c.tenant_id,
    object_links.c.link_type_api_name,
    object_links.c.is_active,
    object_links.c.deleted,
    object_links.c.from_api_name,
    object_links.c.from_object_id,
    object_links.c.to_object_id,
)
Index(
    "ix_object_links_to_active",
    object_links.c.tenant_id,
    object_links.c.link_type_api_name,
    object_links.c.is_active,
    object_links.c.deleted,
    object_links.c.to_api_name,
    object_links.c.to_object_id,
    object_links.c.from_object_id,
)
Index(
    "ix_object_links_properties_gin",
    object_links.c.properties,
    postgresql_using="gin",
    postgresql_ops={"properties": "jsonb_path_ops"},
).ddl_if(dialect="postgresql")


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
    Column("patch", postgres_json_document_type(), nullable=False),
    Column("previous_values", postgres_json_document_type(), nullable=False),
    Column("revert_payload", postgres_json_document_type()),
    Column("actor_user_id", String),
    Column("idempotency_key", String),
    Column("created_at", String, nullable=False),
)

Index(
    "ix_object_edits_timeline",
    object_edits.c.tenant_id,
    object_edits.c.object_type_api_name,
    object_edits.c.object_id,
    object_edits.c.created_at,
)


object_conflicts = Table(
    "object_conflicts",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("object_type_id", String, nullable=False),
    Column("object_id", String, nullable=False),
    Column("property_api_name", String, nullable=False),
    Column("source_value", postgres_json_document_type()),
    Column("edit_value", postgres_json_document_type()),
    Column("source_dataset_version_id", String),
    Column("edit_id", String),
    Column("status", String, nullable=False),
    Column("created_at", String, nullable=False),
)

Index(
    "ix_object_conflicts_open",
    object_conflicts.c.tenant_id,
    object_conflicts.c.object_type_id,
    object_conflicts.c.object_id,
    object_conflicts.c.status,
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
    Column("execution_idempotency_key", String),
    Column("review_metadata", JSON, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    UniqueConstraint("tenant_id", "created_idempotency_key", name="uq_insight_review_create_idempotency"),
    UniqueConstraint("tenant_id", "assignment_idempotency_key", name="uq_insight_review_assignment_idempotency"),
    UniqueConstraint("tenant_id", "decision_idempotency_key", name="uq_insight_review_decision_idempotency"),
    UniqueConstraint("tenant_id", "execution_idempotency_key", name="uq_insight_review_execution_idempotency"),
)


object_sets = Table(
    "object_sets",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("name", String, nullable=False),
    Column("object_type_id", String, nullable=False),
    Column("set_type", String, nullable=False),
    Column("definition", postgres_json_document_type(), nullable=False),
    Column("visibility", String, nullable=False),
    Column("owner_user_id", String),
    Column("expires_at", String),
    Column("created_at", String, nullable=False),
)
