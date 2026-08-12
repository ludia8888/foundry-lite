"""Durable external delivery intent and outcome rows for Governed Release."""

from __future__ import annotations

from sqlalchemy import JSON, CheckConstraint, Column, Index, Integer, String, Table, UniqueConstraint

from foundry_lite.infrastructure.schema.base import metadata

governed_release_deliveries = Table(
    "governed_release_deliveries",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("application_id", String, nullable=False),
    Column("proposal_id", String, nullable=False),
    Column("release_kind", String, nullable=False),
    Column("workflow_run_id", String, nullable=False),
    Column("parent_delivery_id", String),
    Column("provider", String, nullable=False),
    Column("operation", String, nullable=False),
    Column("status", String, nullable=False),
    Column("target_ref", JSON, nullable=False),
    Column("candidate_ref", JSON),
    Column("environment", String, nullable=False),
    Column("idempotency_key", String, nullable=False),
    Column("request_fingerprint", String, nullable=False),
    Column("provider_operation_id", String),
    Column("provider_resource_id", String),
    Column("prior_resource_id", String),
    Column("result_ref", JSON),
    Column("error_ref", JSON),
    Column("ai_run_id", String, nullable=False),
    Column("binding_hash", String, nullable=False),
    Column("execution_attempt", Integer, nullable=False),
    Column("request_id", String, nullable=False),
    Column("created_by", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("updated_at", String, nullable=False),
    Column("dispatch_started_at", String),
    Column("completed_at", String),
    CheckConstraint(
        "operation IN ('source_publish', 'source_merge', 'application_deploy', 'application_rollback')",
        name="ck_governed_release_delivery_operation",
    ),
    CheckConstraint(
        "status IN ('prepared', 'dispatching', 'landed', 'absent', 'ambiguous', 'failed')",
        name="ck_governed_release_delivery_status",
    ),
    CheckConstraint(
        "release_kind IN ('ontology', 'pipeline')",
        name="ck_governed_release_delivery_release_kind",
    ),
    CheckConstraint(
        "(operation = 'source_publish' AND parent_delivery_id IS NULL AND workflow_run_id = ai_run_id) "
        "OR (operation <> 'source_publish' AND parent_delivery_id IS NOT NULL)",
        name="ck_governed_release_delivery_lineage_shape",
    ),
    CheckConstraint("execution_attempt >= 0", name="ck_governed_release_delivery_attempt"),
    UniqueConstraint(
        "tenant_id",
        "provider",
        "operation",
        "idempotency_key",
        name="uq_governed_release_delivery_idempotency",
    ),
    Index(
        "ix_governed_release_delivery_proposal",
        "tenant_id",
        "proposal_id",
        "created_at",
    ),
    Index(
        "ix_governed_release_delivery_reconcile",
        "tenant_id",
        "status",
        "updated_at",
    ),
    Index(
        "ix_governed_release_delivery_workflow",
        "tenant_id",
        "application_id",
        "workflow_run_id",
        "created_at",
    ),
)


__all__ = ["governed_release_deliveries"]
