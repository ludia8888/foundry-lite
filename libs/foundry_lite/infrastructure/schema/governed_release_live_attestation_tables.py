"""Append-only hosted Governed Release verification attestations."""

from __future__ import annotations

from sqlalchemy import JSON, CheckConstraint, Column, Index, String, Table, UniqueConstraint

from foundry_lite.infrastructure.schema.base import metadata

governed_release_live_attestations = Table(
    "governed_release_live_attestations",
    metadata,
    Column("id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("application_id", String, nullable=False),
    Column("collector_run_id", String, nullable=False),
    Column("schema_version", String, nullable=False),
    Column("status", String, nullable=False),
    Column("attestation_fingerprint", String, nullable=False),
    Column("manifest_digest", String, nullable=False),
    Column("evidence_digest", String, nullable=False),
    Column("configuration_fingerprint", String, nullable=False),
    Column("collector_version", String, nullable=False),
    Column("source_revision", String, nullable=False),
    Column("runtime_profile", String, nullable=False),
    Column("database_backend", String, nullable=False),
    Column("source_provider_profile", String, nullable=False),
    Column("deployment_provider_profile", String, nullable=False),
    Column("ontology_workflow_run_id", String, nullable=False),
    Column("pipeline_workflow_run_id", String, nullable=False),
    Column("ontology_proposal_id", String, nullable=False),
    Column("pipeline_proposal_id", String, nullable=False),
    Column("evidence_json", JSON, nullable=False),
    Column("checks_json", JSON, nullable=False),
    Column("request_id", String, nullable=False),
    Column("created_by", String, nullable=False),
    Column("collected_at", String, nullable=False),
    Column("valid_until", String, nullable=False),
    CheckConstraint("status = 'live_verified'", name="ck_governed_release_live_attestation_status"),
    UniqueConstraint(
        "tenant_id",
        "application_id",
        "collector_run_id",
        name="uq_governed_release_live_attestation_run",
    ),
    Index(
        "ix_governed_release_live_attestation_latest",
        "tenant_id",
        "application_id",
        "collected_at",
    ),
)


__all__ = ["governed_release_live_attestations"]
