"""Right-to-erasure request, certificate, and redaction tables."""

from __future__ import annotations

from sqlalchemy import JSON, Column, String, Table, UniqueConstraint

from foundry_lite.infrastructure.schema.base import metadata

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
