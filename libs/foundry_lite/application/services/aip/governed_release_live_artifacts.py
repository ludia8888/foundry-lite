"""Build golden artifacts only from server-selected DB and provider proof."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import NoReturn, cast

from foundry_lite.application.ports.governed_release_delivery_config import GovernedReleaseDeliveryConfig
from foundry_lite.application.ports.release_delivery_repository import ReleaseDeliveryRecord
from foundry_lite.application.services.aip.governed_release_live_artifact_sources import (
    ACTION_SEQUENCE,
    LiveArtifactSource,
    deployment_artifact,
    validated_artifact_source,
)
from foundry_lite.application.services.aip.governed_release_live_collection_contract import (
    DeliveryOperation,
    ReleaseKind,
    ReleaseTool,
)
from foundry_lite.application.services.aip.governed_release_live_collection_db_types import (
    ServerActionResultClaim,
    ServerLoadedDatabaseSnapshot,
)
from foundry_lite.application.services.aip.governed_release_live_evidence import (
    EVIDENCE_SCHEMA,
    LIVE_PREFLIGHT_ORIGIN,
    MANIFEST_SCHEMA,
    PREFLIGHT_SCHEMA,
    RELEASE_SCOPE,
    verify_golden_evidence,
)
from foundry_lite.application.services.aip.governed_release_live_provider_collector import LiveProviderSnapshot
from foundry_lite.domain.errors import ConflictDetected

JsonObject = Mapping[str, object]
ActionKey = tuple[ReleaseKind, ReleaseTool]
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_KINDS: tuple[ReleaseKind, ...] = ("ontology", "pipeline")


@dataclass(frozen=True, slots=True)
class GovernedReleaseLiveArtifacts:
    """Immutable references to self-verified, JSON-serializable artifacts."""

    manifest: JsonObject
    evidence: JsonObject
    preflight: JsonObject


def build_governed_release_live_artifacts(
    snapshot: ServerLoadedDatabaseSnapshot,
    provider_snapshot: LiveProviderSnapshot,
    config: GovernedReleaseDeliveryConfig,
    golden_run_id: str,
) -> GovernedReleaseLiveArtifacts:
    """Build v2 artifacts without accepting caller evidence or a live flag."""

    run_id = _text_value(golden_run_id, "golden_run_id_missing")
    source = validated_artifact_source(snapshot, provider_snapshot, config)
    manifest = _manifest(source, run_id)
    evidence = _evidence(source, run_id)
    preflight = _preflight(source)
    verified = verify_golden_evidence(manifest, evidence, preflight)
    if not verified.is_structurally_complete:
        _invalid("generated_artifacts_not_structurally_complete")
    if verified.blockers != ("authentic_live_collector_required",):
        _invalid("generated_artifact_provenance_mismatch")
    return GovernedReleaseLiveArtifacts(*(_immutable(item) for item in (manifest, evidence, preflight)))


def _manifest(source: LiveArtifactSource, run_id: str) -> dict[str, object]:
    repository = source.config.source_repository
    assert repository is not None
    return {
        "schemaVersion": MANIFEST_SCHEMA,
        "runId": run_id,
        "applicationId": source.db.application_id,
        "publicBaseUrl": source.public_base,
        "authorizationServer": source.issuer,
        "hostedClientId": source.client_id,
        "requiredScope": RELEASE_SCOPE,
        "resource": source.resource,
        "sourceControl": {
            "provider": repository.provider,
            "repositoryId": repository.repository_id,
            "owner": repository.owner,
            "repository": repository.name,
            "baseRef": source.config.source_base_ref,
            "publications": [_source_binding(source, kind) for kind in _KINDS],
        },
        "deployment": {
            "provider": source.records[("pipeline", "application_deploy")].provider,
            "serviceId": source.config.deployment_service_id,
            "environment": source.config.deployment_environment,
        },
        "requiredScenarios": ["ontology", "pipeline"],
    }


def _evidence(source: LiveArtifactSource, run_id: str) -> dict[str, object]:
    return {
        "schemaVersion": EVIDENCE_SCHEMA,
        "runId": run_id,
        "applicationId": source.db.application_id,
        "capture": {
            "host": "chatgpt.com",
            "publicEndpoint": source.resource,
            "transportProfile": "hosted_chatgpt_public_https",
            "verificationProfile": "provider_live_readback",
            "isSimulated": False,
            "completedAt": source.provider.completed_at.isoformat(),
        },
        "principals": _principals(source),
        "scenarios": [_ontology(source), _pipeline(source)],
    }


def _preflight(source: LiveArtifactSource) -> dict[str, object]:
    repository = source.config.source_repository
    assert repository is not None
    configuration = {
        "publicBaseUrl": source.public_base,
        "authorizationServer": source.issuer,
        "resource": source.resource,
        "repository": f"{repository.owner}/{repository.name}",
        "repositoryId": repository.repository_id,
        "baseBranch": source.config.source_base_ref,
        "serviceId": source.config.deployment_service_id,
        "providerPolicyRequestId": source.provider.target_configuration.provider_request_id,
    }
    return {
        "schema_version": PREFLIGHT_SCHEMA,
        "status": "ready",
        "is_ready": True,
        "network_mode": "read_only",
        "evidence_origin": LIVE_PREFLIGHT_ORIGIN,
        "checks": [
            {
                "name": "configuration",
                "status": "ready",
                "code": "exact_targets_configured",
                "evidence": configuration,
            }
        ],
        "unverified": [],
    }


def _principals(source: LiveArtifactSource) -> dict[str, object]:
    common = {
        "applicationId": source.db.application_id,
        "clientId": source.client_id,
        "issuer": source.issuer,
        "audience": source.resource,
        "grantType": "authorization_code",
        "oauthSessionAuthority": "issuer",
        "isHuman": True,
    }
    return {
        "submitter": {
            **common,
            "subjectHash": source.db.submitter_subject_hash,
            "oauthSessionHash": source.db.submitter_oauth_session_hash,
        },
        "reviewer": {
            **common,
            "subjectHash": source.db.reviewer_subject_hash,
            "oauthSessionHash": source.db.reviewer_oauth_session_hash,
        },
    }


def _ontology(source: LiveArtifactSource) -> dict[str, object]:
    execute = _result(source, "ontology", "execute_approved_release")
    rollback = _result(source, "ontology", "rollback_release")
    active = _mapping(_mapping(execute, "releaseEvidence"), "activeOntology")
    target = _mapping(execute, "rollbackTarget")
    rolled_back = _mapping(_mapping(rollback, "lastOperation"), "result")
    active_version = _positive(active, "versionNumber")
    target_version = _positive(target, "targetVersionNumber")
    if execute.get("stage") != "active" or rollback.get("stage") != "superseded":
        _invalid("ontology_state_mismatch")
    if rolled_back.get("rolled_back_to_version_number") != target_version:
        _invalid("ontology_rollback_target_mismatch")
    return {
        "kind": "ontology",
        "governance": _governance(source, "ontology"),
        "sourcePublication": _publication(source, "ontology"),
        "sourceControl": _merge(source, "ontology"),
        "internal": {
            "status": "active",
            "activeVersionNumber": active_version,
            "auditEventId": _audit(source, "ontology", "execute_approved_release"),
        },
        "rollback": {
            "status": "rolled_back",
            "targetVersionNumber": target_version,
            "auditEventId": _audit(source, "ontology", "rollback_release"),
        },
    }


def _pipeline(source: LiveArtifactSource) -> dict[str, object]:
    execute = _result(source, "pipeline", "execute_approved_release")
    deploy = _result(source, "pipeline", "deploy_release")
    rollback = _result(source, "pipeline", "rollback_release")
    current = _mapping(_mapping(deploy, "releaseEvidence"), "currentDeployment")
    valid = execute.get("stage") == "merged" and deploy.get("stage") == "deployed"
    valid = valid and rollback.get("stage") == "superseded" and current.get("status") == "PROMOTED"
    if not valid:
        _invalid("pipeline_state_mismatch")
    return {
        "kind": "pipeline",
        "governance": _governance(source, "pipeline"),
        "sourcePublication": _publication(source, "pipeline"),
        "sourceControl": _merge(source, "pipeline"),
        "internal": {
            "status": "PROMOTED",
            "deploymentId": _text(current, "id"),
            "auditEventId": _audit(source, "pipeline", "deploy_release"),
        },
        "deployment": _deployment(source, "application_deploy"),
        "statusObservation": _status(source),
        "rollback": _rollback(source),
    }


def _governance(source: LiveArtifactSource, kind: ReleaseKind) -> dict[str, object]:
    decision = _result(source, kind, "submit_release_decision")
    candidate = _mapping(decision, "candidate")
    fingerprint = _proposal_fingerprint(source, kind)
    raw = candidate.get("fingerprint" if kind == "ontology" else "graphFingerprint")
    observed = raw if kind == "ontology" else f"sha256:{raw}"
    if decision.get("stage") != "approved" or observed != fingerprint:
        _invalid("governance_decision_mismatch")
    _validation(decision)
    return {
        "proposalId": _proposal(source.db, kind),
        "proposalFingerprint": fingerprint,
        "validationPassed": True,
        "decision": "approve",
        "submitterSubjectHash": source.db.submitter_subject_hash,
        "reviewerSubjectHash": source.db.reviewer_subject_hash,
        "auditEventIds": [_audit(source, kind, tool) for tool in ACTION_SEQUENCE[kind]],
    }


def _validation(decision: JsonObject) -> None:
    rows = _validation_rows(_mapping(decision, "releaseEvidence").get("validationEvidence"))
    if any(item.get("status") not in {"passed", "warning"} for item in rows):
        _invalid("validation_evidence_failed")
    if not any(_is_external_ci_proof(item) for item in rows):
        _invalid("external_ci_evidence_missing")


def _validation_rows(value: object) -> tuple[JsonObject, ...]:
    if not isinstance(value, list) or not value:
        _invalid("validation_evidence_missing")
    if not all(isinstance(item, Mapping) for item in value):
        _invalid("validation_evidence_failed")
    return tuple(cast(JsonObject, item) for item in value)


def _is_external_ci_proof(item: JsonObject) -> bool:
    return (
        item.get("proofKind") == "source_control_merge_result_or_head_required_checks"
        and item.get("status") == "passed"
    )


def _source_binding(source: LiveArtifactSource, kind: ReleaseKind) -> dict[str, object]:
    row = source.records[(kind, "source_publish")]
    target, candidate, result = row.target_ref, _required_mapping(row.candidate_ref), _required_mapping(row.result_ref)
    repository = source.config.source_repository
    assert repository is not None
    expected = {
        "repositoryId": repository.repository_id,
        "repositoryOwner": repository.owner,
        "repositoryName": repository.name,
        "baseRef": source.config.source_base_ref,
    }
    if any(target.get(key) != value for key, value in expected.items()):
        _invalid("source_publication_target_mismatch")
    binding: dict[str, object] = {
        "kind": kind,
        "operation": "source_publish",
        "proposalId": _proposal(source.db, kind),
        "provider": row.provider,
        "repositoryId": repository.repository_id,
        "owner": repository.owner,
        "repository": repository.name,
        "baseRef": source.config.source_base_ref,
        "baseSha": _git(target, "baseSha"),
        "headRef": _text(target, "headRef"),
        "headSha": _git(result, "headSha"),
        "treeSha": _git(result, "treeSha"),
        "pullNumber": _positive(result, "pullNumber"),
        "manifestPath": _text(candidate, "artifactPath"),
        "manifestFingerprint": _sha(candidate, "manifestFingerprint"),
    }
    _source_consistency(binding, result, candidate)
    return binding


def _publication(source: LiveArtifactSource, kind: ReleaseKind) -> dict[str, object]:
    row, observed = source.records[(kind, "source_publish")], source.observations[(kind, "source_publish")]
    binding = _source_binding(source, kind)
    if observed.evidence.get("pullNumber") != binding["pullNumber"]:
        _invalid("source_publication_readback_mismatch")
    return {
        **binding,
        "deliveryId": row.delivery_id,
        "status": "landed",
        "receiptStatus": "published",
        "providerResourceId": row.provider_resource_id,
        "providerRequestId": observed.provider_request_id,
        "completedAt": _timestamp(row.completed_at, "source_publication_time_invalid"),
    }


def _merge(source: LiveArtifactSource, kind: ReleaseKind) -> dict[str, object]:
    publication, row = source.records[(kind, "source_publish")], source.records[(kind, "source_merge")]
    observed, binding = source.observations[(kind, "source_merge")], _source_binding(source, kind)
    evidence, result = observed.evidence, _required_mapping(row.result_ref)
    actual = (evidence.get("pullNumber"), evidence.get("headSha"), evidence.get("mergeCommitSha"))
    expected = (binding["pullNumber"], binding["headSha"], result.get("mergeCommitSha"))
    if actual != expected or not _checks_passed(row):
        _invalid("source_merge_readback_mismatch")
    return {
        **binding,
        "publicationDeliveryId": publication.delivery_id,
        "mergeCommitSha": _git(evidence, "mergeCommitSha"),
        "isMerged": True,
        "requiredChecksPassed": True,
        "providerRequestId": observed.provider_request_id,
        "mergedAt": _timestamp(evidence.get("mergedAt"), "source_merge_time_invalid"),
    }


def _deployment(source: LiveArtifactSource, operation: DeliveryOperation) -> dict[str, object]:
    return deployment_artifact(source, operation)


def _status(source: LiveArtifactSource) -> dict[str, object]:
    deployed = _deployment(source, "application_deploy")
    return {
        "deployId": deployed["deployId"],
        "commitId": deployed["commitId"],
        "status": "live",
        "providerRequestId": deployed["providerRequestId"],
        "observedAt": deployed["finishedAt"],
    }


def _rollback(source: LiveArtifactSource) -> dict[str, object]:
    row = source.records[("pipeline", "application_rollback")]
    deployed = source.records[("pipeline", "application_deploy")]
    receipt, candidate = _deployment(source, "application_rollback"), _required_mapping(row.candidate_ref)
    target_deploy, target_commit = _text(candidate, "targetDeployId"), _git(candidate, "targetCommitId")
    identities = {target_deploy, deployed.provider_resource_id, row.provider_resource_id}
    if row.prior_resource_id != deployed.provider_resource_id or len(identities) != 3:
        _invalid("deployment_rollback_identity_mismatch")
    return {
        **receipt,
        "targetDeployId": target_deploy,
        "targetCommitId": target_commit,
        "rolledBackFromDeployId": deployed.provider_resource_id,
        "rollbackDeployId": row.provider_resource_id,
        "commitId": target_commit,
        "trigger": "rollback",
    }


def _result(source: LiveArtifactSource, kind: ReleaseKind, tool: ReleaseTool) -> JsonObject:
    return _release(source.db, source.results[(kind, tool)], (kind, tool))


def _release(db: ServerLoadedDatabaseSnapshot, result: ServerActionResultClaim, key: ActionKey) -> JsonObject:
    release = _mapping(result.result_json, "release")
    if (release.get("releaseKind"), release.get("proposalId")) != (key[0], _proposal(db, key[0])):
        _invalid("action_release_identity_mismatch")
    return release


def _source_consistency(binding: JsonObject, result: JsonObject, candidate: JsonObject) -> None:
    expected = {key: binding[key] for key in ("baseRef", "baseSha", "headRef", "manifestFingerprint")}
    if any(result.get(key) != value for key, value in expected.items()):
        _invalid("source_publication_receipt_mismatch")
    if (candidate.get("proposalId"), candidate.get("releaseKind")) != (binding["proposalId"], binding["kind"]):
        _invalid("source_candidate_identity_mismatch")


def _checks_passed(row: ReleaseDeliveryRecord) -> bool:
    candidate = row.candidate_ref or {}
    checks = candidate.get("requiredChecks")
    return bool(
        candidate.get("isReadyToMerge") is True
        and isinstance(checks, list)
        and all(isinstance(item, Mapping) and item.get("isSuccessful") is True for item in checks)
    )


def _proposal_fingerprint(source: LiveArtifactSource, kind: ReleaseKind) -> str:
    return _sha(_required_mapping(source.records[(kind, "source_publish")].candidate_ref), "proposalContentFingerprint")


def _audit(source: LiveArtifactSource, kind: ReleaseKind, tool: ReleaseTool) -> str:
    return source.audits[(kind, tool)].event_id


def _proposal(db: ServerLoadedDatabaseSnapshot, kind: ReleaseKind) -> str:
    return db.ontology_proposal_id if kind == "ontology" else db.pipeline_proposal_id


def _mapping(payload: JsonObject, key: str) -> JsonObject:
    return _required_mapping(payload.get(key))


def _required_mapping(value: object) -> JsonObject:
    if not isinstance(value, Mapping):
        _invalid("required_mapping_missing")
    return value


def _text(payload: JsonObject, key: str) -> str:
    return _text_value(payload.get(key), f"{key}_invalid")


def _text_value(value: object, reason: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid(reason)
    return value.strip()


def _positive(payload: JsonObject, key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        _invalid(f"{key}_invalid")
    return value


def _pattern(payload: JsonObject, key: str, pattern: re.Pattern[str]) -> str:
    value = _text(payload, key)
    if pattern.fullmatch(value) is None:
        _invalid(f"{key}_invalid")
    return value


def _sha(payload: JsonObject, key: str) -> str:
    return _pattern(payload, key, _SHA256)


def _git(payload: JsonObject, key: str) -> str:
    return _pattern(payload, key, _GIT_SHA)


def _timestamp(value: object, reason: str) -> str:
    text = _text_value(value, reason)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        _invalid(reason)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _invalid(reason)
    return text


def _immutable(payload: dict[str, object]) -> JsonObject:
    return cast(JsonObject, MappingProxyType(payload))


def _invalid(reason: str) -> NoReturn:
    raise ConflictDetected("server live artifacts cannot be built", details={"reason": reason})


__all__ = ["GovernedReleaseLiveArtifacts", "build_governed_release_live_artifacts"]
