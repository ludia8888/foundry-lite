from __future__ import annotations

from dataclasses import replace

import pytest
from foundry_lite.application.governed_release_completion_coordinates import GovernedReleaseCompletionReader
from foundry_lite.application.ports.governed_release_delivery_config import GovernedReleaseDeliveryConfig
from foundry_lite.application.ports.governed_release_live_attestation_repository import (
    GovernedReleaseLiveAttestationRecord,
    GovernedReleaseLiveAuthority,
    GovernedReleaseMcpAuthority,
)
from foundry_lite.application.ports.source_control_candidate import SourceRepositoryRef
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.services.aip.fde_tool_result import hash_json
from foundry_lite.application.services.aip.governed_release_live_attestation_service import (
    LIVE_ATTESTATION_SCHEMA,
    GovernedReleaseLiveAttestationService,
    live_configuration_fingerprint,
)
from foundry_lite.application.services.aip.governed_release_live_attestation_writer import (
    PreparedLiveAttestation,
)
from foundry_lite.application.services.aip.governed_release_live_collection_contract import (
    LiveCollectionContractResult,
)
from foundry_lite.application.services.aip.governed_release_live_evidence import (
    GoldenEvidenceVerification,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.governed_release_live_attestation_repository import (
    SqlAlchemyGovernedReleaseLiveAttestationRepository,
)
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

_MCP_AUTHORITY = GovernedReleaseMcpAuthority(
    application_id="application-1",
    public_base_url="https://foundry.example.test/",
    authorization_server_issuer="https://identity.example.test/",
    oauth_audience="https://foundry.example.test/mcp/release/application-1",
    allowed_client_ids=("chatgpt-release-client",),
)


def test_eligible_server_without_attestation_is_ready_but_not_live_verified() -> None:
    service, _repository, _engine = _service()

    result = service.live_readiness(_context(), "application-1")

    assert result["status"] == "ready_for_live_run"
    assert result["is_ready_for_live_run"] is True
    assert result["is_live_verified"] is False
    assert result["blockers"] == ["authentic_live_attestation_missing"]


def test_exact_current_database_attestation_is_the_only_live_verified_path() -> None:
    service, repository, engine = _service()
    record = _record(service)
    with engine.begin() as transaction:
        repository.store_verified(transaction=transaction, record=record)

    result = service.live_readiness(_context(), "application-1")
    other_tenant = service.live_readiness(
        RequestContext(tenant_id="tenant-b", actor_user_id="operator-1"),
        "application-1",
    )

    assert result["status"] == "live_verified"
    assert result["is_live_verified"] is True
    assert result["attestation"] == {
        "attestationPurpose": "rollback_rehearsal",
        "attestationId": "attestation-1",
        "collectorRunId": "collector-1",
        "collectorVersion": "governed-release-live-collector/v1",
        "sourceRevision": "1" * 40,
        "collectedAt": "2026-08-09T12:00:00Z",
        "validUntil": "2099-09-09T12:00:00Z",
        "ontologyWorkflowRunId": "ontology-workflow-1",
        "pipelineWorkflowRunId": "pipeline-workflow-1",
    }
    assert other_tenant["is_live_verified"] is False


def test_changed_server_configuration_or_expired_attestation_fails_closed() -> None:
    service, repository, engine = _service()
    with engine.begin() as transaction:
        repository.store_verified(transaction=transaction, record=_record(service))
    stale_service, _unused_repository, _unused_engine = _service(
        engine=engine,
        authority=replace(service.governed_release_live_authority, source_revision="2" * 40),
    )
    expired_service, expired_repository, expired_engine = _service()
    expired_record = replace(
        _record(expired_service),
        attestation_id="attestation-expired",
        collector_run_id="collector-expired",
        attestation_fingerprint=_digest("e"),
        collected_at="2000-01-01T00:00:00Z",
        valid_until="2000-01-01T00:01:00Z",
    )
    with expired_engine.begin() as transaction:
        expired_repository.store_verified(transaction=transaction, record=expired_record)

    stale = stale_service.live_readiness(_context(), "application-1")
    expired = expired_service.live_readiness(_context(), "application-1")

    assert stale["blockers"] == ["authentic_live_attestation_configuration_stale"]
    assert expired["blockers"] == ["authentic_live_attestation_expired"]


@pytest.mark.parametrize(
    "changed_mcp_authority",
    [
        replace(
            _MCP_AUTHORITY,
            public_base_url="https://new-foundry.example.test",
            oauth_audience="https://new-foundry.example.test/mcp/release/application-1",
        ),
        replace(_MCP_AUTHORITY, authorization_server_issuer="https://new-identity.example.test"),
        replace(_MCP_AUTHORITY, allowed_client_ids=("chatgpt-release-client", "new-client")),
    ],
)
def test_current_public_oauth_configuration_drift_invalidates_live_readiness(
    changed_mcp_authority: GovernedReleaseMcpAuthority,
) -> None:
    service, repository, engine = _service()
    with engine.begin() as transaction:
        repository.store_verified(transaction=transaction, record=_record(service))
    changed_service, _unused_repository, _unused_engine = _service(
        engine=engine,
        authority=replace(service.governed_release_live_authority, mcp_authority=changed_mcp_authority),
    )

    result = changed_service.live_readiness(_context(), "application-1")

    assert result["is_live_verified"] is False
    assert result["blockers"] == ["authentic_live_attestation_configuration_stale"]


@pytest.mark.parametrize(
    "invalid_mcp_authority",
    [
        replace(
            _MCP_AUTHORITY,
            application_id="other-application",
            oauth_audience="https://foundry.example.test/mcp/release/other-application",
        ),
        replace(_MCP_AUTHORITY, oauth_audience="https://foundry.example.test/mcp/release/other-application"),
    ],
)
def test_wrong_configured_application_or_audience_blocks_live_collector(
    invalid_mcp_authority: GovernedReleaseMcpAuthority,
) -> None:
    service, _repository, _engine = _service(
        authority=replace(
            GovernedReleaseLiveAuthority(
                runtime_profile="protected",
                database_backend="postgresql",
                source_provider_profile="github-release",
                deployment_provider_profile="render-infrastructure-deployment",
                source_revision="1" * 40,
                mcp_authority=_MCP_AUTHORITY,
            ),
            mcp_authority=invalid_mcp_authority,
        )
    )

    result = service.live_readiness(_context(), "application-1")

    assert result["status"] == "blocked"
    assert result["blockers"] == ["authentic_live_collector_not_eligible"]


def test_local_or_fake_authority_is_blocked_before_attestation_lookup() -> None:
    local = GovernedReleaseLiveAuthority(
        runtime_profile="local",
        database_backend="sqlite",
        source_provider_profile="fake-github",
        deployment_provider_profile="fake-render",
        source_revision="",
    )
    service, _repository, _engine = _service(authority=local)

    result = service.live_readiness(_context(), "application-1")

    assert result["status"] == "blocked"
    assert result["is_ready_for_live_run"] is False
    assert result["blockers"] == ["authentic_live_collector_not_eligible"]


def test_ineligible_completion_coordinates_stop_before_workflow_ledger_scan() -> None:
    local = GovernedReleaseLiveAuthority(
        runtime_profile="local",
        database_backend="sqlite",
        source_provider_profile="fake-github",
        deployment_provider_profile="fake-render",
        source_revision="",
    )
    service, _repository, _engine = _service(authority=local)

    reader = GovernedReleaseCompletionReader(
        ai_run_repository=service.ai_run_repository,
        engine=service.engine,
        governed_release_live_authority=service.governed_release_live_authority,
        release_delivery_repository=service.release_delivery_repository,
        runtime_repository=service.runtime_repository,
    )
    result = reader.completion_coordinates(_context(), "application-1")

    assert result["isEligible"] is False
    assert result["nextAction"] is None
    assert result["reason"] == "authentic_live_collector_not_eligible"


def test_verified_insert_writes_one_atomic_audit_and_outbox_on_exact_replay() -> None:
    service, _repository, engine = _service()
    evidence = _EvidenceBoundary()
    service.bind_collaborators({"runtime_service": evidence})
    prepared = _prepared(_record(service))

    with engine.begin() as transaction:
        first, is_created = service._store_prepared(transaction, _context(), prepared)
    with engine.begin() as transaction:
        replay, is_replay_created = service._store_prepared(transaction, _context(), prepared)

    assert is_created is True
    assert is_replay_created is False
    assert replay.attestation_id == first.attestation_id
    assert len(evidence.audits) == 1
    assert len(evidence.outboxes) == 1
    assert evidence.audits[0]["collectorRunId"] == "collector-1"
    assert evidence.outboxes[0]["status"] == "live_verified"


def test_fenced_action_reuses_the_committed_collection_after_a_crash() -> None:
    service, repository, engine = _service()
    ctx = _action_context()
    collection_id, golden_run_id, replay = service._collection_replay(
        ctx, "application-1", "ontology-workflow-1", "pipeline-workflow-1"
    )
    record = replace(
        _record(service),
        attestation_id="attestation-replayed",
        collector_run_id=collection_id,
        attestation_fingerprint=_digest("f"),
    )
    with engine.begin() as transaction:
        repository.store_verified(transaction=transaction, record=record)

    repeated_collection, repeated_golden, replay = service._collection_replay(
        ctx, "application-1", "ontology-workflow-1", "pipeline-workflow-1"
    )

    assert replay is not None
    assert replay["isCreated"] is False
    assert replay["collectorRunId"] == collection_id
    assert (repeated_collection, repeated_golden) == (collection_id, golden_run_id)


def test_committed_collection_cannot_replay_through_another_oauth_client() -> None:
    service, repository, engine = _service(
        authority=GovernedReleaseLiveAuthority(
            runtime_profile="protected",
            database_backend="postgresql",
            source_provider_profile="github-release",
            deployment_provider_profile="render-infrastructure-deployment",
            source_revision="1" * 40,
            mcp_authority=replace(
                _mcp_authority(),
                allowed_client_ids=("chatgpt-release-client", "other-client"),
            ),
        )
    )
    ctx = _action_context()
    collection_id, _golden_run_id, _replay = service._collection_replay(
        ctx, "application-1", "ontology-workflow-1", "pipeline-workflow-1"
    )
    with engine.begin() as transaction:
        repository.store_verified(
            transaction=transaction,
            record=replace(_record(service), collector_run_id=collection_id),
        )

    with pytest.raises(ConflictDetected) as exc_info:
        service._collection_replay(
            replace(ctx, client_id="other-client"),
            "application-1",
            "ontology-workflow-1",
            "pipeline-workflow-1",
        )

    assert exc_info.value.details["reason"] == "live_collection_replay_oauth_authority_mismatch"


class _EvidenceBoundary:
    def __init__(self) -> None:
        self.audits: list[dict[str, object]] = []
        self.outboxes: list[dict[str, object]] = []

    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        **kwargs: object,
    ) -> None:
        del conn, ctx
        after = kwargs.get("after_ref")
        assert isinstance(after, dict)
        self.audits.append(after)

    def _outbox(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, object],
        **kwargs: object,
    ) -> str:
        del conn, ctx, event_type, aggregate_type, aggregate_id, kwargs
        self.outboxes.append(payload)
        return "outbox-1"


def _service(
    *,
    engine: Engine | None = None,
    authority: GovernedReleaseLiveAuthority | None = None,
) -> tuple[GovernedReleaseLiveAttestationService, SqlAlchemyGovernedReleaseLiveAttestationRepository, Engine]:
    database = engine or create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(database)
    repository = SqlAlchemyGovernedReleaseLiveAttestationRepository(database)
    live_authority = authority or GovernedReleaseLiveAuthority(
        runtime_profile="protected",
        database_backend="postgresql",
        source_provider_profile="github-release",
        deployment_provider_profile="render-infrastructure-deployment",
        source_revision="1" * 40,
        mcp_authority=_mcp_authority(),
    )
    service = GovernedReleaseLiveAttestationService(
        ai_run_repository=object(),
        engine=database,
        governed_release_delivery_config=_config(),
        governed_release_live_attestation_repository=repository,
        governed_release_live_authority=live_authority,
        infrastructure_deployment_adapter=object(),
        release_delivery_repository=object(),
        runtime_repository=object(),
        source_control_release_adapter=object(),
    )
    return service, repository, database


def _record(service: GovernedReleaseLiveAttestationService) -> GovernedReleaseLiveAttestationRecord:
    fingerprint = live_configuration_fingerprint(
        "application-1",
        service.governed_release_delivery_config,
        service.governed_release_live_authority,
    )
    return GovernedReleaseLiveAttestationRecord(
        attestation_id="attestation-1",
        tenant_id="tenant-a",
        application_id="application-1",
        collector_run_id="collector-1",
        schema_version=LIVE_ATTESTATION_SCHEMA,
        status="live_verified",
        attestation_fingerprint=_digest("a"),
        manifest_digest=_digest("b"),
        evidence_digest=_digest("c"),
        configuration_fingerprint=fingerprint,
        collector_version="governed-release-live-collector/v1",
        source_revision="1" * 40,
        runtime_profile="protected",
        database_backend="postgresql",
        source_provider_profile="github-release",
        deployment_provider_profile="render-infrastructure-deployment",
        ontology_workflow_run_id="ontology-workflow-1",
        pipeline_workflow_run_id="pipeline-workflow-1",
        ontology_proposal_id="ontology-proposal-1",
        pipeline_proposal_id="pipeline-proposal-1",
        evidence_json=_stored_evidence(),
        checks_json={"allPassed": True},
        request_id="request-1",
        created_by="operator-1",
        collected_at="2026-08-09T12:00:00Z",
        valid_until="2099-09-09T12:00:00Z",
    )


def _prepared(record: GovernedReleaseLiveAttestationRecord) -> PreparedLiveAttestation:
    contract = LiveCollectionContractResult(
        collection_id=record.collector_run_id,
        is_authoritative=True,
        is_chronologically_valid=True,
        is_toctou_stable=True,
        is_attestation_eligible=True,
        blockers=(),
    )
    structural = GoldenEvidenceVerification(
        schema_version="governed-release-golden-verification/v2",
        run_id="golden-run-1",
        application_id=record.application_id,
        status="structurally_complete",
        is_structurally_complete=True,
        is_live_verified=False,
        preflight_evidence_origin="live_provider_readback",
        manifest_digest=record.manifest_digest,
        evidence_digest=record.evidence_digest,
        checks=(),
        blockers=("authentic_live_collector_required",),
    )
    return PreparedLiveAttestation(record, contract, structural)


def _config() -> GovernedReleaseDeliveryConfig:
    return GovernedReleaseDeliveryConfig(
        source_repository=SourceRepositoryRef(
            provider="github",
            repository_id=123,
            owner="acme",
            name="platform",
        ),
        source_base_ref="main",
        source_head_prefix="codex/",
        is_source_control_required=True,
        deployment_service_id="srv-release-123",
        deployment_environment="production",
        is_deployment_required=True,
    )


def _context() -> RequestContext:
    return RequestContext(tenant_id="tenant-a", actor_user_id="operator-1")


def _action_context() -> RequestContext:
    return RequestContext(
        tenant_id="tenant-a",
        actor_user_id="operator-1",
        application_id="application-1",
        client_id="chatgpt-release-client",
        token_scopes=("osdk:connector:governed_release:execute",),
        oauth_session_hash=f"oauth-session:{_digest('8')}",
        oauth_session_authority="issuer",
        authorization_server_issuer="https://identity.example.test",
        oauth_grant_type="authorization_code",
        oauth_resource="https://foundry.example.test/mcp/release/application-1",
        is_human_oauth=True,
        governed_release_run_id="governed_release_run_verify-1",
        governed_release_binding_hash=_digest("9"),
    )


def _mcp_authority() -> GovernedReleaseMcpAuthority:
    return _MCP_AUTHORITY


def _stored_evidence() -> dict[str, object]:
    common = {
        "applicationId": "application-1",
        "clientId": "chatgpt-release-client",
        "issuer": "https://identity.example.test",
        "audience": "https://foundry.example.test/mcp/release/application-1",
        "grantType": "authorization_code",
        "oauthSessionAuthority": "issuer",
        "isHuman": True,
    }
    return {
        "origin": "server_database_provider_readback",
        "manifest": {
            "applicationId": "application-1",
            "publicBaseUrl": "https://foundry.example.test",
            "authorizationServer": "https://identity.example.test",
            "hostedClientId": "chatgpt-release-client",
            "requiredScope": "osdk:connector:governed_release:execute",
            "resource": "https://foundry.example.test/mcp/release/application-1",
        },
        "evidence": {
            "capture": {"publicEndpoint": "https://foundry.example.test/mcp/release/application-1"},
            "principals": {
                "submitter": {**common, "subjectHash": _digest("7"), "oauthSessionHash": _digest("6")},
                "reviewer": {
                    **common,
                    "subjectHash": hash_json({"issuer": "https://identity.example.test", "subject": "operator-1"}),
                    "oauthSessionHash": _digest("8"),
                },
            },
        },
    }


def _digest(character: str) -> str:
    return f"sha256:{character * 64}"
