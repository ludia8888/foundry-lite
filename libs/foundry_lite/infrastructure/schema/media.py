"""Media set, transaction, item, derivative, and content unit tables."""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, Float, Integer, String, Table, UniqueConstraint, text

from foundry_lite.infrastructure.schema.base import metadata

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
    Column("parent_content_unit_id", String),
    Column("source_locator", JSON),
    Column("structure", JSON),
    Column("confidence", Float),
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


media_attachment_associations = Table(
    "media_attachment_associations",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("media_item_version_id", String, nullable=False),
    Column("holder_type", String, nullable=False),
    Column("holder_id", String, nullable=False),
    Column("first_action_run_id", String, nullable=False),
    Column("created_at", String, nullable=False),
    UniqueConstraint(
        "tenant_id",
        "media_item_version_id",
        "holder_type",
        "holder_id",
        name="uq_media_attachment_lifetime_holder",
    ),
)


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
