"""Normalize the server-owned inputs used by the golden artifact builder."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import NoReturn
from urllib.parse import urlsplit

from foundry_lite.application.ports.governed_release_delivery_config import GovernedReleaseDeliveryConfig
from foundry_lite.application.ports.release_delivery_repository import ReleaseDeliveryRecord
from foundry_lite.application.services.aip.fde_tool_result import hash_json
from foundry_lite.application.services.aip.governed_release_live_collection_contract import (
    DeliveryOperation,
    ReleaseKind,
    ReleaseTool,
    ServerActionClaim,
    ServerDeliveryClaim,
)
from foundry_lite.application.services.aip.governed_release_live_collection_db_types import (
    ServerActionAuditClaim,
    ServerActionResultClaim,
    ServerLoadedDatabaseSnapshot,
)
from foundry_lite.application.services.aip.governed_release_live_evidence import RELEASE_SCOPE
from foundry_lite.application.services.aip.governed_release_live_provider_collector import (
    LiveProviderObservation,
    LiveProviderSnapshot,
)
from foundry_lite.application.services.aip.governed_release_live_target_policy import (
    stored_target_configuration_matches,
)
from foundry_lite.domain.errors import ConflictDetected

JsonObject = Mapping[str, object]
ActionKey = tuple[ReleaseKind, ReleaseTool]
DeliveryKey = tuple[ReleaseKind, DeliveryOperation]
ACTION_SEQUENCE: dict[ReleaseKind, tuple[ReleaseTool, ...]] = {
    "ontology": tuple(
        "publish_release_candidate assign_release_reviewer submit_release_decision "
        "execute_approved_release rollback_release".split()
    ),
    "pipeline": tuple(
        "publish_release_candidate assign_release_reviewer submit_release_decision "
        "execute_approved_release deploy_release rollback_release".split()
    ),
}
DELIVERY_KEYS: frozenset[DeliveryKey] = frozenset(
    {
        ("ontology", "source_publish"),
        ("ontology", "source_merge"),
        ("pipeline", "source_publish"),
        ("pipeline", "source_merge"),
        ("pipeline", "application_deploy"),
        ("pipeline", "application_rollback"),
    }
)


@dataclass(frozen=True, slots=True)
class LiveArtifactSource:
    """Validated indexes over one server DB selection and provider pass."""

    db: ServerLoadedDatabaseSnapshot
    provider: LiveProviderSnapshot
    config: GovernedReleaseDeliveryConfig
    public_base: str
    issuer: str
    client_id: str
    resource: str
    results: Mapping[ActionKey, ServerActionResultClaim]
    audits: Mapping[ActionKey, ServerActionAuditClaim]
    records: Mapping[DeliveryKey, ReleaseDeliveryRecord]
    observations: Mapping[DeliveryKey, LiveProviderObservation]


def validated_artifact_source(
    db: ServerLoadedDatabaseSnapshot,
    provider: LiveProviderSnapshot,
    config: GovernedReleaseDeliveryConfig,
) -> LiveArtifactSource:
    """Reject injected, incomplete, or cross-bound server evidence."""

    if type(db) is not ServerLoadedDatabaseSnapshot or type(provider) is not LiveProviderSnapshot:
        raise TypeError("server_loaded_database_and_live_provider_snapshots_required")
    if config.source_repository is None or config.deployment_service_id is None:
        _invalid("live_artifact_targets_missing")
    public_base, issuer, client_id, resource = _authorization(db)
    results, audits = _actions(db)
    records = _records(db)
    observations = _observations(db, provider, records)
    _target_config(db, config, provider, records)
    return LiveArtifactSource(
        db,
        provider,
        config,
        public_base,
        issuer,
        client_id,
        resource,
        results,
        audits,
        records,
        observations,
    )


def _authorization(db: ServerLoadedDatabaseSnapshot) -> tuple[str, str, str, str]:
    policy = db.authorization_policy
    expected = {
        "applicationId": db.application_id,
        "oauthGrantType": "authorization_code",
        "oauthSessionAuthority": "issuer",
        "isHuman": True,
        "requiredScope": RELEASE_SCOPE,
        "origin": "https://chatgpt.com",
    }
    if any(policy.get(key) != value for key, value in expected.items()):
        _invalid("authorization_policy_mismatch")
    if hash_json(policy) != db.authorization_policy_fingerprint:
        _invalid("authorization_policy_fingerprint_mismatch")
    issuer = _text(policy, "authorizationServerIssuer")
    client = _text(policy, "clientId")
    resource = _text(policy, "oauthResource")
    parsed = urlsplit(resource)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path != f"/mcp/release/{db.application_id}":
        _invalid("oauth_resource_mismatch")
    return f"https://{parsed.netloc}", issuer, client, resource


def _actions(
    db: ServerLoadedDatabaseSnapshot,
) -> tuple[dict[ActionKey, ServerActionResultClaim], dict[ActionKey, ServerActionAuditClaim]]:
    expected: set[ActionKey] = {(kind, tool) for kind, tools in ACTION_SEQUENCE.items() for tool in tools}
    claims, results, audits = _action_indexes(db)
    _require_action_sets(expected, claims, results, audits)
    for key, claim in claims.items():
        _require_action_evidence(db, key, claim, results[key], audits[key])
    if set(db.selected_audit_event_ids) != {item.event_id for item in audits.values()}:
        _invalid("selected_audit_set_mismatch")
    return results, audits


def _action_indexes(
    db: ServerLoadedDatabaseSnapshot,
) -> tuple[
    dict[ActionKey, ServerActionClaim],
    dict[ActionKey, ServerActionResultClaim],
    dict[ActionKey, ServerActionAuditClaim],
]:
    claims: dict[ActionKey, ServerActionClaim] = {(item.release_kind, item.tool_name): item for item in db.actions}
    results: dict[ActionKey, ServerActionResultClaim] = {
        (item.release_kind, item.tool_name): item for item in db.action_results
    }
    audits: dict[ActionKey, ServerActionAuditClaim] = {
        (item.release_kind, item.tool_name): item for item in db.action_audits
    }
    return claims, results, audits


def _require_action_sets(
    expected: set[ActionKey],
    claims: Mapping[ActionKey, ServerActionClaim],
    results: Mapping[ActionKey, ServerActionResultClaim],
    audits: Mapping[ActionKey, ServerActionAuditClaim],
) -> None:
    values = (claims, results, audits)
    if any(len(value) != len(expected) or set(value) != expected for value in values):
        _invalid("action_evidence_set_mismatch")


def _require_action_evidence(
    db: ServerLoadedDatabaseSnapshot,
    key: ActionKey,
    claim: ServerActionClaim,
    result: ServerActionResultClaim,
    audit: ServerActionAuditClaim,
) -> None:
    if result.ai_run_id != claim.ai_run_id or audit.ai_run_id != claim.ai_run_id:
        _invalid("action_evidence_run_mismatch")
    if result.result_fingerprint != hash_json(result.result_json) or not audit.event_id:
        _invalid("action_evidence_fingerprint_mismatch")
    _require_release_identity(db, result, key)


def _records(db: ServerLoadedDatabaseSnapshot) -> dict[DeliveryKey, ReleaseDeliveryRecord]:
    records: dict[DeliveryKey, ReleaseDeliveryRecord] = {
        (row.release_kind, row.operation): row for row in db.delivery_records
    }
    claims = {(row.release_kind, row.operation): row for row in db.deliveries}
    if len(records) != len(DELIVERY_KEYS) or set(records) != DELIVERY_KEYS or set(claims) != DELIVERY_KEYS:
        _invalid("delivery_set_mismatch")
    for key, row in records.items():
        claim = claims[key]
        identity = (row.tenant_id, row.application_id, row.proposal_id, row.delivery_id, row.status)
        expected = (db.tenant_id, db.application_id, _proposal(db, key[0]), claim.delivery_id, "landed")
        if identity != expected or claim.result_fingerprint != hash_json(row.result_ref):
            _invalid("delivery_record_binding_mismatch")
    return records


def _observations(
    db: ServerLoadedDatabaseSnapshot,
    provider: LiveProviderSnapshot,
    records: Mapping[DeliveryKey, ReleaseDeliveryRecord],
) -> dict[DeliveryKey, LiveProviderObservation]:
    values: dict[DeliveryKey, LiveProviderObservation] = {
        (item.release_kind, item.operation): item for item in provider.observations
    }
    claims = {(item.release_kind, item.operation): item for item in db.deliveries}
    if len(values) != len(DELIVERY_KEYS) or set(values) != DELIVERY_KEYS:
        _invalid("provider_readback_set_mismatch")
    for key, item in values.items():
        _require_observation_binding(item, records[key], claims[key])
        _require_observation_time(item, provider.completed_at, db.initial_read_at)
    return values


def _require_observation_binding(
    item: LiveProviderObservation,
    row: ReleaseDeliveryRecord,
    claim: ServerDeliveryClaim,
) -> None:
    identity = (item.delivery_id, item.provider, item.provider_resource_id, item.ledger_result_fingerprint)
    expected = (row.delivery_id, row.provider, row.provider_resource_id, claim.result_fingerprint)
    if identity != expected or not item.is_exact_target or not item.is_terminal_success:
        _invalid("provider_readback_binding_mismatch")


def _require_observation_time(item: LiveProviderObservation, completed_at: datetime, initial_read_at: datetime) -> None:
    if not item.provider_request_id or not _ordered(item.observed_at, completed_at, initial_read_at):
        _invalid("provider_readback_time_mismatch")


def _target_config(
    db: ServerLoadedDatabaseSnapshot,
    config: GovernedReleaseDeliveryConfig,
    provider: LiveProviderSnapshot,
    records: Mapping[DeliveryKey, ReleaseDeliveryRecord],
) -> None:
    target, evidence = provider.target_configuration, provider.target_configuration.evidence
    deployment_provider = _deployment_provider(records)
    if deployment_provider is None:
        _invalid("target_configuration_provider_mismatch")
    matches = stored_target_configuration_matches(
        config,
        evidence,
        deployment_provider,
        target.provider_request_id,
    )
    if not target.is_exact_target or not matches:
        _invalid("target_configuration_mismatch")
    if not _ordered(target.observed_at, provider.completed_at, db.initial_read_at):
        _invalid("target_configuration_time_mismatch")


def deployment_artifact(source: LiveArtifactSource, operation: DeliveryOperation) -> dict[str, object]:
    """Separate the original live receipt from the post-rollback readback."""

    row = source.records[("pipeline", operation)]
    observed = source.observations[("pipeline", operation)]
    candidate = _required_mapping(row.candidate_ref, "deployment_candidate_missing")
    commit_key = "commitId" if operation == "application_deploy" else "targetCommitId"
    if operation == "application_deploy":
        _require_historical_deployment(source, observed, candidate)
        evidence = _required_mapping(row.result_ref, "deployment_receipt_missing")
        request_id = _text(evidence, "providerRequestId")
    else:
        evidence = observed.evidence
        request_id = observed.provider_request_id
    expected = _live_deployment_expected(source, row, candidate.get(commit_key), request_id)
    if any(evidence.get(key) != value for key, value in expected.items()):
        _invalid("deployment_readback_mismatch")
    return {
        **{key: value for key, value in expected.items() if key != "providerStatus"},
        "environment": source.config.deployment_environment,
        "finishedAt": _timestamp(evidence, "finishedAt", "deployment_time_invalid"),
    }


def _require_historical_deployment(
    source: LiveArtifactSource,
    observed: LiveProviderObservation,
    candidate: JsonObject,
) -> None:
    evidence = observed.evidence
    expected = {
        "provider": observed.provider,
        "serviceId": source.config.deployment_service_id,
        "deployId": observed.provider_resource_id,
        "commitId": candidate.get("commitId"),
        "status": "deactivated",
        "providerStatus": "deactivated",
        "isTerminal": True,
        "isSuccessful": False,
        "providerRequestId": observed.provider_request_id,
    }
    if any(evidence.get(key) != value for key, value in expected.items()):
        _invalid("historical_deployment_readback_mismatch")
    _timestamp(evidence, "finishedAt", "historical_deployment_time_invalid")


def _live_deployment_expected(
    source: LiveArtifactSource,
    row: ReleaseDeliveryRecord,
    commit_id: object,
    request_id: str,
) -> dict[str, object]:
    return {
        "provider": row.provider,
        "serviceId": source.config.deployment_service_id,
        "deployId": row.provider_resource_id,
        "commitId": commit_id,
        "status": "live",
        "providerStatus": "live",
        "isTerminal": True,
        "isSuccessful": True,
        "providerRequestId": request_id,
    }


def _required_mapping(value: object, reason: str) -> JsonObject:
    if not isinstance(value, Mapping):
        _invalid(reason)
    return value


def _timestamp(payload: JsonObject, key: str, reason: str) -> str:
    value = _text(payload, key)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _invalid(reason)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _invalid(reason)
    return value


def _require_release_identity(
    db: ServerLoadedDatabaseSnapshot,
    result: ServerActionResultClaim,
    key: ActionKey,
) -> None:
    release = result.result_json.get("release")
    expected = (key[0], _proposal(db, key[0]))
    if not isinstance(release, Mapping) or (release.get("releaseKind"), release.get("proposalId")) != expected:
        _invalid("action_release_identity_mismatch")


def _proposal(db: ServerLoadedDatabaseSnapshot, kind: ReleaseKind) -> str:
    return db.ontology_proposal_id if kind == "ontology" else db.pipeline_proposal_id


def _deployment_provider(records: Mapping[DeliveryKey, ReleaseDeliveryRecord]) -> str | None:
    providers = {row.provider for key, row in records.items() if not key[1].startswith("source_")}
    return providers.pop() if len(providers) == 1 else None


def _text(payload: JsonObject, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        _invalid(f"{key}_invalid")
    return value.strip()


def _ordered(start: datetime, middle: datetime, end: datetime) -> bool:
    values = (start, middle, end)
    is_aware = all(value.tzinfo is not None and value.utcoffset() is not None for value in values)
    return is_aware and start <= middle <= end


def _invalid(reason: str) -> NoReturn:
    raise ConflictDetected("server live artifact inputs are invalid", details={"reason": reason})


__all__ = ["ACTION_SEQUENCE", "LiveArtifactSource", "deployment_artifact", "validated_artifact_source"]
