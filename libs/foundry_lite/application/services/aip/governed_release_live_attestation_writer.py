"""Build immutable live attestations from typed server-owned collection proof."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Protocol, cast

from foundry_lite.application.ports.governed_release_live_attestation_repository import (
    GovernedReleaseLiveAttestationRecord,
    GovernedReleaseLiveAuthority,
)
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.services.aip.governed_release_live_authority import (
    is_exact_live_reviewer_invoker,
    live_artifact_authority_blockers,
)
from foundry_lite.application.services.aip.governed_release_live_collection_contract import (
    DeliveryOperation,
    LiveCollectionContractResult,
    ReleaseKind,
    ServerActionClaim,
    ServerCollectionSeal,
    ServerDeliveryClaim,
    ServerLoadedCollectionClaim,
    ServerProviderReadback,
    assess_server_loaded_collection,
)
from foundry_lite.application.services.aip.governed_release_live_evidence import (
    GoldenEvidenceVerification,
    canonical_digest,
    verify_golden_evidence,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected

JsonObject = Mapping[str, object]
DeliveryKey = tuple[ReleaseKind, DeliveryOperation]
LIVE_ATTESTATION_SCHEMA = "governed-release-live-attestation/v1"
_ATTESTATION_VALIDITY = timedelta(hours=24)
_KINDS: tuple[ReleaseKind, ...] = ("ontology", "pipeline")


@dataclass(frozen=True, slots=True)
class ServerVerifiedLiveCollection:
    """Artifacts assembled internally from the same sealed database collection."""

    claim: ServerLoadedCollectionClaim
    manifest: JsonObject
    evidence: JsonObject
    preflight: JsonObject


@dataclass(frozen=True, slots=True)
class PreparedLiveAttestation:
    record: GovernedReleaseLiveAttestationRecord
    contract: LiveCollectionContractResult
    structural: GoldenEvidenceVerification


class LiveAttestationEvidenceBoundary(Protocol):
    """Audit and outbox writes required beside an attestation insert."""

    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str | None,
        action: str,
        decision: str = "allow",
        policy_decision: Mapping[str, object] | None = None,
        before_ref: Mapping[str, object] | None = None,
        after_ref: Mapping[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> None: ...

    def _outbox(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> str | None: ...


def prepare_live_attestation(
    ctx: RequestContext,
    collection: ServerVerifiedLiveCollection,
    authority: GovernedReleaseLiveAuthority,
    configuration_fingerprint: str,
) -> PreparedLiveAttestation:
    """Re-verify all contracts and prepare one deterministic append-only row."""

    _require_server_collection_type(collection)
    claim = collection.claim
    contract = assess_server_loaded_collection(claim)
    structural = verify_golden_evidence(collection.manifest, collection.evidence, collection.preflight)
    _require_eligible(ctx, claim, contract, structural, authority)
    _require_artifact_bindings(ctx, authority, claim, collection.manifest, collection.evidence)
    record = _record(ctx, collection, authority, configuration_fingerprint, structural, contract)
    return PreparedLiveAttestation(record, contract, structural)


def live_attestation_evidence(record: GovernedReleaseLiveAttestationRecord) -> dict[str, object]:
    """Return the public-safe event payload shared by audit and outbox."""

    return {
        "attestationPurpose": "rollback_rehearsal",
        "attestationId": record.attestation_id,
        "applicationId": record.application_id,
        "collectorRunId": record.collector_run_id,
        "attestationFingerprint": record.attestation_fingerprint,
        "manifestDigest": record.manifest_digest,
        "evidenceDigest": record.evidence_digest,
        "configurationFingerprint": record.configuration_fingerprint,
        "sourceRevision": record.source_revision,
        "status": record.status,
        "collectedAt": record.collected_at,
        "validUntil": record.valid_until,
    }


def _require_server_collection_type(collection: ServerVerifiedLiveCollection) -> None:
    if type(collection) is not ServerVerifiedLiveCollection:
        raise TypeError("server_verified_live_collection_required")
    if type(collection.claim) is not ServerLoadedCollectionClaim:
        raise TypeError("server_loaded_collection_claim_required")


def _require_eligible(
    ctx: RequestContext,
    claim: ServerLoadedCollectionClaim,
    contract: LiveCollectionContractResult,
    structural: GoldenEvidenceVerification,
    authority: GovernedReleaseLiveAuthority,
) -> None:
    blockers: list[str] = []
    if not authority.is_live_eligible_for(claim.application_id):
        blockers.append("authentic_live_collector_not_eligible")
    if (claim.tenant_id, claim.application_id) != (ctx.tenant_id, structural.application_id):
        blockers.append("live_collection_request_scope_mismatch")
    if not is_exact_live_reviewer_invoker(ctx, claim, authority):
        blockers.append("live_collection_reviewer_invoker_mismatch")
    if not contract.is_attestation_eligible or contract.blockers:
        blockers.extend(contract.blockers or ("typed_live_collection_contract_failed",))
    if not structural.is_structurally_complete:
        blockers.extend(structural.blockers)
    if structural.blockers != ("authentic_live_collector_required",):
        blockers.append("live_structural_provenance_incomplete")
    if blockers:
        raise ConflictDetected("server live collection is not attestation eligible", details={"blockers": blockers})


def _require_artifact_bindings(
    ctx: RequestContext,
    authority: GovernedReleaseLiveAuthority,
    claim: ServerLoadedCollectionClaim,
    manifest: JsonObject,
    evidence: JsonObject,
) -> None:
    blockers = [
        *live_artifact_authority_blockers(ctx, authority, claim.application_id, manifest, evidence),
        *_root_binding_blockers(claim, manifest, evidence),
        *_principal_binding_blockers(claim, evidence),
        *_scenario_binding_blockers(claim, evidence),
    ]
    if blockers:
        raise ConflictDetected(
            "live artifacts do not match the sealed server collection",
            details={"blockers": blockers},
        )


def _root_binding_blockers(
    claim: ServerLoadedCollectionClaim,
    manifest: JsonObject,
    evidence: JsonObject,
) -> list[str]:
    expected = (claim.golden_run_id, claim.application_id)
    manifest_identity = (manifest.get("runId"), manifest.get("applicationId"))
    evidence_identity = (evidence.get("runId"), evidence.get("applicationId"))
    return [] if manifest_identity == evidence_identity == expected else ["artifact_collection_identity_mismatch"]


def _principal_binding_blockers(claim: ServerLoadedCollectionClaim, evidence: JsonObject) -> list[str]:
    principals = _mapping(evidence, "principals")
    submitter = _mapping(principals, "submitter")
    reviewer = _mapping(principals, "reviewer")
    expected_submitter = (claim.submitter_subject_hash, claim.submitter_oauth_session_hash)
    expected_reviewer = (claim.reviewer_subject_hash, claim.reviewer_oauth_session_hash)
    actual_submitter = (submitter.get("subjectHash"), submitter.get("oauthSessionHash"))
    actual_reviewer = (reviewer.get("subjectHash"), reviewer.get("oauthSessionHash"))
    return (
        []
        if (actual_submitter, actual_reviewer) == (expected_submitter, expected_reviewer)
        else ["artifact_principal_binding_mismatch"]
    )


def _scenario_binding_blockers(claim: ServerLoadedCollectionClaim, evidence: JsonObject) -> list[str]:
    scenarios = _scenario_map(evidence)
    deliveries: dict[DeliveryKey, ServerDeliveryClaim] = {
        (item.release_kind, item.operation): item for item in claim.deliveries
    }
    blockers: list[str] = []
    for kind in _KINDS:
        scenario = scenarios.get(kind)
        proposal_ids = {item.proposal_id for item in claim.actions if item.release_kind == kind}
        if scenario is None or len(proposal_ids) != 1:
            blockers.append(f"{kind}_artifact_scenario_missing")
            continue
        blockers.extend(_source_artifact_blockers(kind, scenario, proposal_ids.pop(), deliveries))
    blockers.extend(_pipeline_artifact_blockers(scenarios.get("pipeline"), deliveries))
    return blockers


def _source_artifact_blockers(
    kind: ReleaseKind,
    scenario: JsonObject,
    proposal_id: str,
    deliveries: Mapping[DeliveryKey, ServerDeliveryClaim],
) -> list[str]:
    publication = deliveries.get((kind, "source_publish"))
    merge = deliveries.get((kind, "source_merge"))
    governance = _mapping(scenario, "governance")
    published = _mapping(scenario, "sourcePublication")
    merged = _mapping(scenario, "sourceControl")
    valid = bool(
        publication
        and merge
        and governance.get("proposalId") == proposal_id
        and published.get("deliveryId") == publication.delivery_id
        and published.get("providerResourceId") == publication.provider_resource_id
        and merged.get("publicationDeliveryId") == publication.delivery_id
        and f"pull:{merged.get('pullNumber')}" == merge.provider_resource_id
    )
    return [] if valid else [f"{kind}_artifact_delivery_binding_mismatch"]


def _pipeline_artifact_blockers(
    scenario: JsonObject | None,
    deliveries: Mapping[DeliveryKey, ServerDeliveryClaim],
) -> list[str]:
    deploy = deliveries.get(("pipeline", "application_deploy"))
    rollback = deliveries.get(("pipeline", "application_rollback"))
    if scenario is None or deploy is None or rollback is None:
        return ["pipeline_artifact_infrastructure_binding_mismatch"]
    deployment = _mapping(scenario, "deployment")
    rolled_back = _mapping(scenario, "rollback")
    valid = (
        deployment.get("deployId") == deploy.provider_resource_id
        and rolled_back.get("rollbackDeployId") == rollback.provider_resource_id
    )
    return [] if valid else ["pipeline_artifact_infrastructure_binding_mismatch"]


def _record(
    ctx: RequestContext,
    collection: ServerVerifiedLiveCollection,
    authority: GovernedReleaseLiveAuthority,
    configuration_fingerprint: str,
    structural: GoldenEvidenceVerification,
    contract: LiveCollectionContractResult,
) -> GovernedReleaseLiveAttestationRecord:
    claim = collection.claim
    attestation_fingerprint = canonical_digest(
        _fingerprint_payload(claim, authority, configuration_fingerprint, structural)
    )
    return _build_record(
        ctx,
        claim,
        authority,
        configuration_fingerprint,
        structural,
        attestation_fingerprint,
        _stored_evidence(collection),
        _stored_checks(contract, structural),
    )


def _build_record(
    ctx: RequestContext,
    claim: ServerLoadedCollectionClaim,
    authority: GovernedReleaseLiveAuthority,
    configuration_fingerprint: str,
    structural: GoldenEvidenceVerification,
    attestation_fingerprint: str,
    evidence: JsonObject,
    checks: JsonObject,
) -> GovernedReleaseLiveAttestationRecord:
    return GovernedReleaseLiveAttestationRecord(
        attestation_id=f"live-attestation-{attestation_fingerprint.removeprefix('sha256:')[:32]}",
        tenant_id=claim.tenant_id,
        application_id=claim.application_id,
        collector_run_id=claim.collection_id,
        schema_version=LIVE_ATTESTATION_SCHEMA,
        status="live_verified",
        attestation_fingerprint=attestation_fingerprint,
        manifest_digest=structural.manifest_digest,
        evidence_digest=structural.evidence_digest,
        configuration_fingerprint=configuration_fingerprint,
        collector_version=authority.collector_version,
        source_revision=authority.source_revision,
        runtime_profile=authority.runtime_profile,
        database_backend=authority.database_backend,
        source_provider_profile=authority.source_provider_profile,
        deployment_provider_profile=authority.deployment_provider_profile,
        ontology_workflow_run_id=_workflow_root(claim, "ontology"),
        pipeline_workflow_run_id=_workflow_root(claim, "pipeline"),
        ontology_proposal_id=_proposal_id(claim, "ontology"),
        pipeline_proposal_id=_proposal_id(claim, "pipeline"),
        evidence_json=evidence,
        checks_json=checks,
        request_id=ctx.request_id,
        created_by=ctx.actor_user_id,
        collected_at=claim.collection_completed_at.isoformat(),
        valid_until=(claim.collection_completed_at + _ATTESTATION_VALIDITY).isoformat(),
    )


def _fingerprint_payload(
    claim: ServerLoadedCollectionClaim,
    authority: GovernedReleaseLiveAuthority,
    configuration_fingerprint: str,
    structural: GoldenEvidenceVerification,
) -> dict[str, object]:
    return {
        "schemaVersion": LIVE_ATTESTATION_SCHEMA,
        "tenantId": claim.tenant_id,
        "applicationId": claim.application_id,
        "collectorRunId": claim.collection_id,
        "goldenRunId": claim.golden_run_id,
        "manifestDigest": structural.manifest_digest,
        "evidenceDigest": structural.evidence_digest,
        "configurationFingerprint": configuration_fingerprint,
        "databaseFingerprint": claim.seal.final_database_fingerprint,
        "targetConfigurationFingerprint": claim.seal.final_target_configuration_fingerprint,
        "authorizationPolicyFingerprint": claim.authorization_policy_fingerprint,
        "sourceRevision": authority.source_revision,
        "collectorVersion": authority.collector_version,
    }


def _stored_evidence(collection: ServerVerifiedLiveCollection) -> dict[str, object]:
    claim = collection.claim
    return {
        "origin": "server_database_provider_readback",
        "manifest": _json_object(collection.manifest),
        "evidence": _json_object(collection.evidence),
        "preflight": _json_object(collection.preflight),
        "serverCollection": _claim_projection(claim),
    }


def _stored_checks(
    contract: LiveCollectionContractResult,
    structural: GoldenEvidenceVerification,
) -> dict[str, object]:
    return {
        "allPassed": True,
        "typedCollection": asdict(contract),
        "structuralVerification": {
            "schemaVersion": structural.schema_version,
            "status": structural.status,
            "isStructurallyComplete": structural.is_structurally_complete,
            "checks": [asdict(item) for item in structural.checks],
            "blockers": list(structural.blockers),
        },
    }


def _claim_projection(claim: ServerLoadedCollectionClaim) -> dict[str, object]:
    return {
        "authority": claim.authority,
        "collectionId": claim.collection_id,
        "goldenRunId": claim.golden_run_id,
        "databaseSystem": claim.database_system,
        "providerReadbackMode": claim.provider_readback_mode,
        "authorizationPolicyFingerprint": claim.authorization_policy_fingerprint,
        "actions": [_dataclass_json(item) for item in claim.actions],
        "deliveries": [_dataclass_json(item) for item in claim.deliveries],
        "providerReadbacks": [_dataclass_json(item) for item in claim.provider_readbacks],
        "seal": _dataclass_json(claim.seal),
        "collectionStartedAt": claim.collection_started_at.isoformat(),
        "collectionCompletedAt": claim.collection_completed_at.isoformat(),
    }


def _dataclass_json(
    value: ServerActionClaim | ServerDeliveryClaim | ServerProviderReadback | ServerCollectionSeal,
) -> dict[str, object]:
    raw = asdict(value)
    normalized = {key: item.isoformat() if hasattr(item, "isoformat") else item for key, item in raw.items()}
    return cast(dict[str, object], normalized)


def _workflow_root(claim: ServerLoadedCollectionClaim, kind: str) -> str:
    rows = [item.workflow_run_id for item in claim.deliveries if item.release_kind == kind]
    values = set(rows)
    if len(values) != 1:
        raise ConflictDetected("live scenario workflow root is ambiguous")
    return values.pop()


def _proposal_id(claim: ServerLoadedCollectionClaim, kind: str) -> str:
    values = {item.proposal_id for item in claim.actions if item.release_kind == kind}
    if len(values) != 1:
        raise ConflictDetected("live scenario proposal is ambiguous")
    return values.pop()


def _scenario_map(evidence: JsonObject) -> dict[str, JsonObject]:
    values = evidence.get("scenarios")
    if not isinstance(values, list):
        return {}
    return {
        str(value.get("kind")): value
        for value in values
        if isinstance(value, Mapping) and value.get("kind") in {"ontology", "pipeline"}
    }


def _mapping(payload: JsonObject, key: str) -> JsonObject:
    value = payload.get(key)
    return value if isinstance(value, Mapping) else {}


def _json_object(payload: JsonObject) -> dict[str, object]:
    encoded = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise TypeError("server collection artifact must be a JSON object")
    return cast(dict[str, object], decoded)


__all__ = [
    "LIVE_ATTESTATION_SCHEMA",
    "LiveAttestationEvidenceBoundary",
    "PreparedLiveAttestation",
    "ServerVerifiedLiveCollection",
    "live_attestation_evidence",
    "prepare_live_attestation",
]
