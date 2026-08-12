"""SQLAlchemy adapter for immutable hosted Governed Release attestations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from sqlalchemy import and_, insert, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine

from foundry_lite.application.ports.governed_release_live_attestation_repository import (
    GovernedReleaseLiveAttestationConflict,
    GovernedReleaseLiveAttestationIntegrityError,
    GovernedReleaseLiveAttestationMutationResult,
    GovernedReleaseLiveAttestationRecord,
    LiveAttestationJson,
    LiveAttestationStatus,
)
from foundry_lite.infrastructure import schema as db


class SqlAlchemyGovernedReleaseLiveAttestationRepository:
    """Insert verified proof once and keep every read tenant/application scoped."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def store_verified(
        self,
        *,
        transaction: Any,
        record: GovernedReleaseLiveAttestationRecord,
    ) -> GovernedReleaseLiveAttestationMutationResult:
        inserted_id = transaction.execute(_insert_or_ignore(transaction, _values(record))).scalar_one_or_none()
        if inserted_id is not None:
            inserted = self.get_by_collector_run(
                transaction=transaction,
                tenant_id=record.tenant_id,
                application_id=record.application_id,
                collector_run_id=record.collector_run_id,
            )
            if inserted is None:
                raise GovernedReleaseLiveAttestationIntegrityError("inserted live attestation was not readable")
            return inserted, True
        existing = self.get_by_collector_run(
            transaction=transaction,
            tenant_id=record.tenant_id,
            application_id=record.application_id,
            collector_run_id=record.collector_run_id,
        )
        if existing is None:
            raise GovernedReleaseLiveAttestationIntegrityError("ignored live attestation had no winning row")
        if existing.attestation_fingerprint != record.attestation_fingerprint:
            raise GovernedReleaseLiveAttestationConflict("collector run was replayed with different evidence")
        return existing, False

    def get_by_collector_run(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        application_id: str,
        collector_run_id: str,
    ) -> GovernedReleaseLiveAttestationRecord | None:
        table = db.governed_release_live_attestations
        row = (
            transaction.execute(
                select(table).where(
                    and_(
                        table.c.tenant_id == tenant_id,
                        table.c.application_id == application_id,
                        table.c.collector_run_id == collector_run_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        return _record(row) if row is not None else None

    def latest_verified(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        application_id: str,
    ) -> GovernedReleaseLiveAttestationRecord | None:
        table = db.governed_release_live_attestations
        row = (
            transaction.execute(
                select(table)
                .where(
                    and_(
                        table.c.tenant_id == tenant_id,
                        table.c.application_id == application_id,
                        table.c.status == "live_verified",
                    )
                )
                .order_by(table.c.collected_at.desc(), table.c.id.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        return _record(row) if row is not None else None


def _insert_or_ignore(transaction: Any, values: Mapping[str, object]) -> Any:
    table = db.governed_release_live_attestations
    if transaction.dialect.name == "postgresql":
        return postgres_insert(table).values(**values).on_conflict_do_nothing().returning(table.c.id)
    if transaction.dialect.name == "sqlite":
        return sqlite_insert(table).values(**values).on_conflict_do_nothing().returning(table.c.id)
    return insert(table).values(**values).returning(table.c.id)


def _values(record: GovernedReleaseLiveAttestationRecord) -> dict[str, object]:
    return {
        "id": record.attestation_id,
        "tenant_id": record.tenant_id,
        "application_id": record.application_id,
        "collector_run_id": record.collector_run_id,
        "schema_version": record.schema_version,
        "status": record.status,
        "attestation_fingerprint": record.attestation_fingerprint,
        "manifest_digest": record.manifest_digest,
        "evidence_digest": record.evidence_digest,
        "configuration_fingerprint": record.configuration_fingerprint,
        "collector_version": record.collector_version,
        "source_revision": record.source_revision,
        "runtime_profile": record.runtime_profile,
        "database_backend": record.database_backend,
        "source_provider_profile": record.source_provider_profile,
        "deployment_provider_profile": record.deployment_provider_profile,
        "ontology_workflow_run_id": record.ontology_workflow_run_id,
        "pipeline_workflow_run_id": record.pipeline_workflow_run_id,
        "ontology_proposal_id": record.ontology_proposal_id,
        "pipeline_proposal_id": record.pipeline_proposal_id,
        "evidence_json": dict(record.evidence_json),
        "checks_json": dict(record.checks_json),
        "request_id": record.request_id,
        "created_by": record.created_by,
        "collected_at": record.collected_at,
        "valid_until": record.valid_until,
    }


def _record(row: Mapping[str, object]) -> GovernedReleaseLiveAttestationRecord:
    return GovernedReleaseLiveAttestationRecord(
        attestation_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        application_id=str(row["application_id"]),
        collector_run_id=str(row["collector_run_id"]),
        schema_version=str(row["schema_version"]),
        status=cast(LiveAttestationStatus, row["status"]),
        attestation_fingerprint=str(row["attestation_fingerprint"]),
        manifest_digest=str(row["manifest_digest"]),
        evidence_digest=str(row["evidence_digest"]),
        configuration_fingerprint=str(row["configuration_fingerprint"]),
        collector_version=str(row["collector_version"]),
        source_revision=str(row["source_revision"]),
        runtime_profile=str(row["runtime_profile"]),
        database_backend=str(row["database_backend"]),
        source_provider_profile=str(row["source_provider_profile"]),
        deployment_provider_profile=str(row["deployment_provider_profile"]),
        ontology_workflow_run_id=str(row["ontology_workflow_run_id"]),
        pipeline_workflow_run_id=str(row["pipeline_workflow_run_id"]),
        ontology_proposal_id=str(row["ontology_proposal_id"]),
        pipeline_proposal_id=str(row["pipeline_proposal_id"]),
        evidence_json=cast(LiveAttestationJson, row["evidence_json"]),
        checks_json=cast(LiveAttestationJson, row["checks_json"]),
        request_id=str(row["request_id"]),
        created_by=str(row["created_by"]),
        collected_at=str(row["collected_at"]),
        valid_until=str(row["valid_until"]),
    )


__all__ = ["SqlAlchemyGovernedReleaseLiveAttestationRepository"]
