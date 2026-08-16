from __future__ import annotations

from dataclasses import replace

import pytest
from foundry_lite.application.ports.governed_release_live_attestation_repository import (
    GovernedReleaseLiveAttestationConflict,
    GovernedReleaseLiveAttestationRecord,
    GovernedReleaseLiveAttestationRepository,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.governed_release_live_attestation_repository import (
    SqlAlchemyGovernedReleaseLiveAttestationRepository,
)
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def test_store_verified_is_append_only_and_exactly_replayable() -> None:
    repository, engine = _repository()
    with engine.begin() as transaction:
        created, is_created = repository.store_verified(transaction=transaction, record=_record())
        replayed, is_replay_created = repository.store_verified(
            transaction=transaction,
            record=replace(
                _record(),
                attestation_id="attestation-replay",
                request_id="request-retry",
                collected_at="2026-08-09T12:01:00Z",
            ),
        )
        with pytest.raises(GovernedReleaseLiveAttestationConflict):
            repository.store_verified(
                transaction=transaction,
                record=replace(
                    _record(),
                    attestation_id="attestation-conflict",
                    attestation_fingerprint=_digest("f"),
                ),
            )

    assert is_created is True
    assert is_replay_created is False
    assert replayed.attestation_id == created.attestation_id
    assert replayed.request_id == "request-1"


def test_reads_are_tenant_and_application_scoped_and_latest_is_deterministic() -> None:
    repository, engine = _repository()
    with engine.begin() as transaction:
        repository.store_verified(transaction=transaction, record=_record())
        repository.store_verified(
            transaction=transaction,
            record=replace(
                _record(),
                attestation_id="attestation-2",
                collector_run_id="collector-2",
                attestation_fingerprint=_digest("e"),
                collected_at="2026-08-09T12:02:00Z",
            ),
        )
        wrong_tenant = repository.get_by_collector_run(
            transaction=transaction,
            tenant_id="tenant-b",
            application_id="application-1",
            collector_run_id="collector-1",
        )
        wrong_application = repository.latest_verified(
            transaction=transaction,
            tenant_id="tenant-a",
            application_id="application-2",
        )
        latest = repository.latest_verified(
            transaction=transaction,
            tenant_id="tenant-a",
            application_id="application-1",
        )

    assert wrong_tenant is None
    assert wrong_application is None
    assert latest is not None
    assert latest.collector_run_id == "collector-2"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("status", "blocked"),
        ("runtime_profile", "local"),
        ("database_backend", "sqlite"),
        ("source_provider_profile", ""),
        ("deployment_provider_profile", ""),
        ("ontology_workflow_run_id", ""),
        ("pipeline_proposal_id", ""),
        ("collected_at", "2026-08-09T12:00:00"),
        ("valid_until", None),
        ("valid_until", "2026-08-09T11:59:59Z"),
    ],
)
def test_record_rejects_non_live_authority_profiles(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        replace(_record(), **{field: value})


def _repository() -> tuple[GovernedReleaseLiveAttestationRepository, Engine]:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    return SqlAlchemyGovernedReleaseLiveAttestationRepository(engine), engine


def _record() -> GovernedReleaseLiveAttestationRecord:
    return GovernedReleaseLiveAttestationRecord(
        attestation_id="attestation-1",
        tenant_id="tenant-a",
        application_id="application-1",
        collector_run_id="collector-1",
        schema_version="governed-release-live-attestation/v1",
        status="live_verified",
        attestation_fingerprint=_digest("a"),
        manifest_digest=_digest("b"),
        evidence_digest=_digest("c"),
        configuration_fingerprint=_digest("d"),
        collector_version="collector/v1",
        source_revision="1" * 40,
        runtime_profile="protected",
        database_backend="postgresql",
        source_provider_profile="github-release",
        deployment_provider_profile="render-infrastructure-deployment",
        ontology_workflow_run_id="ontology-workflow-1",
        pipeline_workflow_run_id="pipeline-workflow-1",
        ontology_proposal_id="ontology-proposal-1",
        pipeline_proposal_id="pipeline-proposal-1",
        evidence_json={"source": "server_collector"},
        checks_json={"allPassed": True},
        request_id="request-1",
        created_by="operator-1",
        collected_at="2026-08-09T12:00:00Z",
        valid_until="2026-09-09T12:00:00Z",
    )


def _digest(character: str) -> str:
    return f"sha256:{character * 64}"
