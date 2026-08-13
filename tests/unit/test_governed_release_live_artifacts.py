from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

import pytest
from foundry_lite.application.ports.governed_release_delivery_config import GovernedReleaseDeliveryConfig
from foundry_lite.application.ports.governed_release_live_attestation_repository import (
    GovernedReleaseLiveAuthority,
    GovernedReleaseMcpAuthority,
)
from foundry_lite.application.ports.release_delivery_repository import (
    ReleaseDeliveryKind,
    ReleaseDeliveryOperation,
    ReleaseDeliveryRecord,
)
from foundry_lite.application.ports.source_control_release import SourceRepositoryRef
from foundry_lite.application.services.aip.fde_tool_result import hash_json
from foundry_lite.application.services.aip.governed_release_live_artifact_sources import ACTION_SEQUENCE
from foundry_lite.application.services.aip.governed_release_live_artifacts import (
    build_governed_release_live_artifacts,
)
from foundry_lite.application.services.aip.governed_release_live_attestation_service import (
    live_configuration_fingerprint,
)
from foundry_lite.application.services.aip.governed_release_live_attestation_writer import (
    ServerVerifiedLiveCollection,
    prepare_live_attestation,
)
from foundry_lite.application.services.aip.governed_release_live_collection import (
    build_server_loaded_collection_claim,
)
from foundry_lite.application.services.aip.governed_release_live_collection_contract import (
    ReleaseKind,
    ServerActionClaim,
    ServerDeliveryClaim,
)
from foundry_lite.application.services.aip.governed_release_live_collection_db_types import (
    ServerActionAuditClaim,
    ServerActionResultClaim,
    ServerLoadedDatabaseSnapshot,
)
from foundry_lite.application.services.aip.governed_release_live_evidence import verify_golden_evidence
from foundry_lite.application.services.aip.governed_release_live_provider_collector import (
    LiveProviderObservation,
    LiveProviderSnapshot,
    LiveTargetConfigurationObservation,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected

TENANT = "tenant-live"
APPLICATION = "release-app"
RESOURCE = f"https://foundry.example.test/mcp/release/{APPLICATION}"
ISSUER = "https://identity.example.test"
CLIENT = "chatgpt-release-client"
REPOSITORY = SourceRepositoryRef("github", 42, "acme", "foundry-lite")
CONFIG = GovernedReleaseDeliveryConfig(
    source_repository=REPOSITORY,
    source_base_ref="main",
    is_source_control_required=True,
    deployment_service_id="srv-foundry-lite",
    deployment_environment="production",
    is_deployment_required=True,
)
START = datetime(2026, 8, 9, 1, 0, tzinfo=UTC)
READBACK = START + timedelta(hours=1)
ONTOLOGY_HEAD = "a" * 40
ONTOLOGY_TREE = "b" * 40
ONTOLOGY_MERGE = "c" * 40
PIPELINE_HEAD = "d" * 40
PIPELINE_TREE = "e" * 40
PIPELINE_MERGE = "f" * 40
PRIOR_COMMIT = "7" * 40


def test_builder_creates_only_structurally_complete_server_artifacts() -> None:
    snapshot, provider = _inputs()

    artifacts = build_governed_release_live_artifacts(snapshot, provider, CONFIG, "golden-run-live")
    verified = verify_golden_evidence(artifacts.manifest, artifacts.evidence, artifacts.preflight)

    assert verified.is_structurally_complete is True
    assert verified.blockers == ("authentic_live_collector_required",)
    assert artifacts.preflight["evidence_origin"] == "live_provider_readback"
    with pytest.raises(TypeError):
        cast(dict[str, object], artifacts.manifest)["runId"] = "caller-forged"


def test_server_collection_builds_and_prepares_a_live_attestation() -> None:
    final_database, collection, authority = _server_collection()
    prepared = prepare_live_attestation(
        _reviewer_context(final_database),
        collection,
        authority,
        live_configuration_fingerprint(APPLICATION, CONFIG, authority),
    )

    assert prepared.record.status == "live_verified"
    assert prepared.contract.blockers == ()
    assert prepared.structural.is_structurally_complete is True
    assert prepared.structural.blockers == ("authentic_live_collector_required",)


def test_current_public_resource_drift_cannot_create_live_attestation() -> None:
    final_database, collection, authority = _server_collection()
    ctx = replace(
        _reviewer_context(final_database),
        oauth_resource="https://other.example.test/mcp/release/release-app",
    )

    with pytest.raises(ConflictDetected) as exc_info:
        prepare_live_attestation(
            ctx,
            collection,
            authority,
            live_configuration_fingerprint(APPLICATION, CONFIG, authority),
        )

    assert "live_collection_reviewer_invoker_mismatch" in exc_info.value.details["blockers"]


def test_same_reviewer_session_from_another_allowed_client_cannot_reuse_artifacts() -> None:
    final_database, collection, authority = _server_collection()
    changed_authority = replace(
        authority,
        mcp_authority=replace(authority.mcp_authority, allowed_client_ids=(CLIENT, "other-client")),
    )
    ctx = replace(_reviewer_context(final_database), client_id="other-client")

    with pytest.raises(ConflictDetected) as exc_info:
        prepare_live_attestation(
            ctx,
            collection,
            changed_authority,
            live_configuration_fingerprint(APPLICATION, CONFIG, changed_authority),
        )

    assert "artifact_startup_oauth_authority_mismatch" in exc_info.value.details["blockers"]


def test_public_base_drift_rejects_artifacts_from_the_old_host() -> None:
    final_database, collection, authority = _server_collection()
    changed_mcp = replace(
        authority.mcp_authority,
        public_base_url="https://new-foundry.example.test",
        oauth_audience="https://new-foundry.example.test/mcp/release/release-app",
    )
    changed_authority = replace(authority, mcp_authority=changed_mcp)
    ctx = replace(_reviewer_context(final_database), oauth_resource=changed_mcp.release_resource(APPLICATION))

    with pytest.raises(ConflictDetected) as exc_info:
        prepare_live_attestation(
            ctx,
            collection,
            changed_authority,
            live_configuration_fingerprint(APPLICATION, CONFIG, changed_authority),
        )

    assert "artifact_startup_oauth_authority_mismatch" in exc_info.value.details["blockers"]


def test_builder_rejects_non_chatgpt_authorization_policy() -> None:
    snapshot, provider = _inputs()
    policy = {**snapshot.authorization_policy, "origin": "https://attacker.example"}
    changed = replace(snapshot, authorization_policy=policy, authorization_policy_fingerprint=hash_json(policy))

    with pytest.raises(ConflictDetected) as exc_info:
        build_governed_release_live_artifacts(changed, provider, CONFIG, "golden-run-live")

    assert exc_info.value.details["reason"] == "authorization_policy_mismatch"


def test_builder_rejects_action_result_not_bound_to_selected_proposal() -> None:
    snapshot, provider = _inputs()
    result = snapshot.action_results[0]
    release = dict(cast(dict[str, object], result.result_json["release"]))
    release["proposalId"] = "another-proposal"
    result_json = {"release": release}
    changed_result = replace(result, result_json=result_json, result_fingerprint=hash_json(result_json))
    changed = replace(snapshot, action_results=(changed_result, *snapshot.action_results[1:]))

    with pytest.raises(ConflictDetected) as exc_info:
        build_governed_release_live_artifacts(changed, provider, CONFIG, "golden-run-live")

    assert exc_info.value.details["reason"] == "action_release_identity_mismatch"


def test_builder_rejects_inexact_provider_readback() -> None:
    snapshot, provider = _inputs()
    changed_observation = replace(provider.observations[0], is_exact_target=False)
    changed = replace(provider, observations=(changed_observation, *provider.observations[1:]))

    with pytest.raises(ConflictDetected) as exc_info:
        build_governed_release_live_artifacts(snapshot, changed, CONFIG, "golden-run-live")

    assert exc_info.value.details["reason"] == "provider_readback_binding_mismatch"


def test_builder_rejects_ontology_rollback_not_matching_execute_receipt() -> None:
    snapshot, provider = _inputs()
    key = next(
        index
        for index, item in enumerate(snapshot.action_results)
        if (item.release_kind, item.tool_name) == ("ontology", "rollback_release")
    )
    result = snapshot.action_results[key]
    release = dict(cast(dict[str, object], result.result_json["release"]))
    operation = dict(cast(dict[str, object], release["lastOperation"]))
    operation["result"] = {"rolled_back_to_version_number": 9}
    release["lastOperation"] = operation
    result_json = {"release": release}
    changed_result = replace(result, result_json=result_json, result_fingerprint=hash_json(result_json))
    results = list(snapshot.action_results)
    results[key] = changed_result

    with pytest.raises(ConflictDetected) as exc_info:
        build_governed_release_live_artifacts(
            replace(snapshot, action_results=tuple(results)),
            provider,
            CONFIG,
            "golden-run-live",
        )

    assert exc_info.value.details["reason"] == "ontology_rollback_target_mismatch"


def _inputs() -> tuple[ServerLoadedDatabaseSnapshot, LiveProviderSnapshot]:
    actions, results, audits = _action_evidence()
    records = _delivery_records(actions)
    deliveries = tuple(_delivery_claim(row) for row in records)
    policy = _policy()
    snapshot = ServerLoadedDatabaseSnapshot(
        tenant_id=TENANT,
        application_id=APPLICATION,
        ontology_workflow_run_id=_run_id("ontology", "publish_release_candidate"),
        pipeline_workflow_run_id=_run_id("pipeline", "publish_release_candidate"),
        ontology_proposal_id="ontology-proposal",
        pipeline_proposal_id="pipeline-proposal",
        authorization_policy_fingerprint=hash_json(policy),
        submitter_subject_hash=_subject_hash("submitter"),
        submitter_oauth_session_hash=_digest("submitter-session"),
        reviewer_subject_hash=_subject_hash("reviewer"),
        reviewer_oauth_session_hash=_digest("reviewer-session"),
        is_authorization_code_human_grant=True,
        authorization_policy=policy,
        actions=actions,
        action_results=results,
        action_audits=audits,
        deliveries=deliveries,
        delivery_records=records,
        selected_audit_event_ids=tuple(item.event_id for item in audits),
        initial_database_fingerprint=_digest("database"),
        initial_read_at=READBACK + timedelta(minutes=2),
    )
    return snapshot, _provider_snapshot(records)


def _server_collection() -> tuple[
    ServerLoadedDatabaseSnapshot,
    ServerVerifiedLiveCollection,
    GovernedReleaseLiveAuthority,
]:
    final_database, final_provider = _inputs()
    initial_database = replace(final_database, initial_read_at=START)
    initial_provider = _retime_provider(final_provider, START + timedelta(minutes=10), "initial")
    claim = build_server_loaded_collection_claim(
        initial_database=initial_database,
        final_database=final_database,
        initial_provider=initial_provider,
        final_provider=final_provider,
        collection_id="collection-live",
        golden_run_id="golden-run-live",
        collection_started_at=START - timedelta(minutes=1),
        collection_completed_at=final_database.initial_read_at + timedelta(minutes=1),
    )
    artifacts = build_governed_release_live_artifacts(final_database, final_provider, CONFIG, "golden-run-live")
    collection = ServerVerifiedLiveCollection(claim, artifacts.manifest, artifacts.evidence, artifacts.preflight)
    return final_database, collection, _authority()


def _policy() -> dict[str, object]:
    return {
        "applicationId": APPLICATION,
        "clientId": CLIENT,
        "authorizationServerIssuer": ISSUER,
        "oauthGrantType": "authorization_code",
        "oauthResource": RESOURCE,
        "oauthSessionAuthority": "issuer",
        "isHuman": True,
        "requiredScope": "osdk:connector:governed_release:execute",
        "origin": "https://chatgpt.com",
    }


def _action_evidence() -> tuple[
    tuple[ServerActionClaim, ...],
    tuple[ServerActionResultClaim, ...],
    tuple[ServerActionAuditClaim, ...],
]:
    claims: list[ServerActionClaim] = []
    results: list[ServerActionResultClaim] = []
    audits: list[ServerActionAuditClaim] = []
    for kind, tools in ACTION_SEQUENCE.items():
        for offset, tool in enumerate(tools):
            run_id = _run_id(kind, tool)
            result_json = {"release": _release_result(kind, tool)}
            is_submitter = tool == "publish_release_candidate"
            claims.append(_action_claim(kind, tool, run_id, offset, is_submitter))
            results.append(ServerActionResultClaim(kind, tool, run_id, hash_json(result_json), result_json))
            audits.append(ServerActionAuditClaim(kind, tool, run_id, f"audit-{kind}-{tool}"))
    return tuple(claims), tuple(results), tuple(audits)


def _action_claim(
    kind: ReleaseKind,
    tool: str,
    run_id: str,
    offset: int,
    is_submitter: bool,
) -> ServerActionClaim:
    started = START - timedelta(hours=2) + timedelta(minutes=offset * 5)
    subject = _subject_hash("submitter" if is_submitter else "reviewer")
    session = _digest("submitter-session" if is_submitter else "reviewer-session")
    return ServerActionClaim(
        kind,
        f"{kind}-proposal",
        tool,
        run_id,
        _digest(f"binding-{kind}-{tool}"),
        hash_json(_policy()),
        subject,
        session,
        f"mcp-{'submitter' if is_submitter else 'reviewer'}-{kind}",
        f"idempotency-{kind}-{tool}",
        "succeeded",
        started,
        started + timedelta(minutes=4),
    )


def _release_result(kind: ReleaseKind, tool: str) -> dict[str, object]:
    release: dict[str, object] = {"releaseKind": kind, "proposalId": f"{kind}-proposal", "stage": "awaiting_review"}
    if tool == "submit_release_decision":
        fingerprint = _digest(f"{kind}-proposal-content")
        release.update(
            {
                "stage": "approved",
                "candidate": {
                    "fingerprint" if kind == "ontology" else "graphFingerprint": fingerprint.removeprefix("sha256:")
                    if kind == "pipeline"
                    else fingerprint
                },
                "releaseEvidence": {"validationEvidence": _validation_evidence()},
            }
        )
    if (kind, tool) == ("ontology", "execute_approved_release"):
        release.update(
            {
                "stage": "active",
                "releaseEvidence": {"activeOntology": {"versionNumber": 2}},
                "rollbackTarget": {"targetVersionNumber": 1},
            }
        )
    if (kind, tool) == ("ontology", "rollback_release"):
        release.update({"stage": "superseded", "lastOperation": {"result": {"rolled_back_to_version_number": 1}}})
    if (kind, tool) == ("pipeline", "execute_approved_release"):
        release["stage"] = "merged"
    if (kind, tool) == ("pipeline", "deploy_release"):
        release.update(
            {
                "stage": "deployed",
                "releaseEvidence": {"currentDeployment": {"id": "pipeline-deployment-current", "status": "PROMOTED"}},
            }
        )
    if (kind, tool) == ("pipeline", "rollback_release"):
        release["stage"] = "superseded"
    return release


def _validation_evidence() -> list[dict[str, object]]:
    return [
        {"status": "passed", "proofKind": "durable_candidate_validation"},
        {"status": "passed", "proofKind": "github_merge_result_or_head_required_checks"},
    ]


def _delivery_records(actions: tuple[ServerActionClaim, ...]) -> tuple[ReleaseDeliveryRecord, ...]:
    action_map = {(item.release_kind, item.tool_name): item for item in actions}
    ontology_publish = _source_publication("ontology", 17, ONTOLOGY_HEAD, ONTOLOGY_TREE, action_map)
    ontology_merge = _source_merge(ontology_publish, ONTOLOGY_MERGE, action_map)
    pipeline_publish = _source_publication("pipeline", 18, PIPELINE_HEAD, PIPELINE_TREE, action_map)
    pipeline_merge = _source_merge(pipeline_publish, PIPELINE_MERGE, action_map)
    deploy = _application_delivery(pipeline_merge, "application_deploy", "dep-current", PIPELINE_MERGE, action_map)
    rollback = _application_delivery(deploy, "application_rollback", "dep-rollback", PRIOR_COMMIT, action_map)
    return ontology_publish, ontology_merge, pipeline_publish, pipeline_merge, deploy, rollback


def _source_publication(
    kind: ReleaseKind,
    pull_number: int,
    head_sha: str,
    tree_sha: str,
    actions: dict[tuple[ReleaseKind, str], ServerActionClaim],
) -> ReleaseDeliveryRecord:
    proposal = f"{kind}-proposal"
    target = {
        "repositoryId": REPOSITORY.repository_id,
        "repositoryOwner": REPOSITORY.owner,
        "repositoryName": REPOSITORY.name,
        "baseRef": "main",
        "headRef": f"codex/{proposal}",
        "baseSha": "8" * 40,
    }
    candidate = {
        "releaseKind": kind,
        "proposalId": proposal,
        "artifactPath": f".foundry-lite/releases/{kind}/{proposal}.json",
        "manifestFingerprint": _digest(f"manifest-{kind}"),
        "proposalContentFingerprint": _digest(f"{kind}-proposal-content"),
    }
    result = {
        "status": "published",
        **target,
        **{key: candidate[key] for key in ("manifestFingerprint",)},
        "headSha": head_sha,
        "treeSha": tree_sha,
        "pullNumber": pull_number,
    }
    action = actions[(kind, "publish_release_candidate")]
    return _record(kind, "source_publish", action, None, f"pull:{pull_number}", target, candidate, result)


def _source_merge(
    publication: ReleaseDeliveryRecord,
    merge_sha: str,
    actions: dict[tuple[ReleaseKind, str], ServerActionClaim],
) -> ReleaseDeliveryRecord:
    result = publication.result_ref or {}
    candidate = {
        "baseSha": publication.target_ref["baseSha"],
        "headRef": publication.target_ref["headRef"],
        "candidateTreeSha": result["treeSha"],
        "manifestArtifactPath": cast(dict[str, object], publication.candidate_ref)["artifactPath"],
        "manifestFingerprint": cast(dict[str, object], publication.candidate_ref)["manifestFingerprint"],
        "isReadyToMerge": True,
        "requiredChecks": [{"context": "quality", "isSuccessful": True}],
    }
    target = {
        "repositoryId": REPOSITORY.repository_id,
        "repositoryOwner": REPOSITORY.owner,
        "repositoryName": REPOSITORY.name,
        "pullNumber": result["pullNumber"],
        "baseRef": "main",
        "headSha": result["headSha"],
    }
    action = actions[(publication.release_kind, "execute_approved_release")]
    return _record(
        publication.release_kind,
        "source_merge",
        action,
        publication,
        publication.provider_resource_id,
        target,
        candidate,
        {"mergeCommitSha": merge_sha},
    )


def _application_delivery(
    parent: ReleaseDeliveryRecord,
    operation: ReleaseDeliveryOperation,
    deploy_id: str,
    commit_id: str,
    actions: dict[tuple[ReleaseKind, str], ServerActionClaim],
) -> ReleaseDeliveryRecord:
    tool = "deploy_release" if operation == "application_deploy" else "rollback_release"
    candidate: dict[str, object] = (
        {"commitId": commit_id}
        if operation == "application_deploy"
        else {"targetDeployId": "dep-prior", "targetCommitId": commit_id}
    )
    result = _render_evidence(
        deploy_id, commit_id, "api" if operation == "application_deploy" else "rollback", f"initial-{deploy_id}"
    )
    return _record(
        "pipeline",
        operation,
        actions[("pipeline", tool)],
        parent,
        deploy_id,
        {"serviceId": "srv-foundry-lite"},
        candidate,
        result,
        prior_resource_id="dep-prior" if operation == "application_deploy" else "dep-current",
    )


def _record(
    kind: ReleaseKind,
    operation: ReleaseDeliveryOperation,
    action: ServerActionClaim,
    parent: ReleaseDeliveryRecord | None,
    provider_resource_id: str | None,
    target: dict[str, object],
    candidate: dict[str, object],
    result: dict[str, object],
    *,
    prior_resource_id: str | None = None,
) -> ReleaseDeliveryRecord:
    delivery_id = f"delivery-{kind}-{operation}"
    return ReleaseDeliveryRecord(
        delivery_id,
        TENANT,
        APPLICATION,
        f"{kind}-proposal",
        cast(ReleaseDeliveryKind, kind),
        action.ai_run_id if parent is None else parent.workflow_run_id,
        parent.delivery_id if parent else None,
        "github" if operation.startswith("source_") else "render",
        operation,
        "landed",
        target,
        candidate,
        "main" if operation.startswith("source_") else "production",
        f"key-{delivery_id}",
        _digest(f"request-{delivery_id}"),
        f"operation-{delivery_id}",
        provider_resource_id,
        prior_resource_id,
        result,
        None,
        action.ai_run_id,
        action.binding_hash,
        1,
        f"request-{delivery_id}",
        "submitter" if operation == "source_publish" else "reviewer",
        action.started_at.isoformat(),
        (action.started_at + timedelta(minutes=2)).isoformat(),
        (action.started_at + timedelta(minutes=1)).isoformat(),
        (action.started_at + timedelta(minutes=2)).isoformat(),
    )


def _delivery_claim(row: ReleaseDeliveryRecord) -> ServerDeliveryClaim:
    return ServerDeliveryClaim(
        row.release_kind,
        row.proposal_id,
        row.operation,
        row.delivery_id,
        row.workflow_run_id,
        row.parent_delivery_id,
        cast(Literal["github", "render"], row.provider),
        cast(str, row.provider_resource_id),
        row.ai_run_id,
        row.binding_hash,
        hash_json(row.result_ref),
        row.status,
        datetime.fromisoformat(cast(str, row.dispatch_started_at)),
        datetime.fromisoformat(cast(str, row.completed_at)),
    )


def _provider_snapshot(records: tuple[ReleaseDeliveryRecord, ...]) -> LiveProviderSnapshot:
    observations = tuple(_provider_observation(row) for row in records)
    evidence = {
        "sourceProvider": "github",
        "repositoryId": 42,
        "repositoryOwner": "acme",
        "repositoryName": "foundry-lite",
        "baseRef": "main",
        "baseCommitSha": "9" * 40,
        "baseTreeSha": "6" * 40,
        "provider": "render",
        "serviceId": "srv-foundry-lite",
        "isAutoDeployEnabled": False,
        "sourceRepositoryOwner": "acme",
        "sourceRepositoryName": "foundry-lite",
        "sourceBranch": "main",
        "serviceType": "web_service",
        "isSuspended": False,
        "providerRequestId": "render-policy-request",
    }
    target = LiveTargetConfigurationObservation(
        _digest("target-config"), True, "render-policy-request", READBACK, evidence
    )
    return LiveProviderSnapshot(observations, target, READBACK + timedelta(minutes=1))


def _retime_provider(snapshot: LiveProviderSnapshot, observed_at: datetime, prefix: str) -> LiveProviderSnapshot:
    observations = tuple(
        replace(item, provider_request_id=f"{prefix}-{item.provider_request_id}", observed_at=observed_at)
        for item in snapshot.observations
    )
    target = replace(
        snapshot.target_configuration,
        provider_request_id=f"{prefix}-{snapshot.target_configuration.provider_request_id}",
        observed_at=observed_at,
    )
    return LiveProviderSnapshot(observations, target, observed_at + timedelta(minutes=1))


def _provider_observation(row: ReleaseDeliveryRecord) -> LiveProviderObservation:
    evidence = _observation_evidence(row)
    return LiveProviderObservation(
        row.release_kind,
        row.operation,
        row.delivery_id,
        cast(Literal["github", "render"], row.provider),
        cast(str, row.provider_resource_id),
        hash_json(row.result_ref),
        _digest(f"evidence-{row.delivery_id}"),
        f"provider-request-{row.delivery_id}",
        True,
        True,
        READBACK,
        evidence,
    )


def _observation_evidence(row: ReleaseDeliveryRecord) -> dict[str, object]:
    if not row.operation.startswith("source_"):
        candidate = row.candidate_ref or {}
        commit = candidate.get("commitId") or candidate.get("targetCommitId")
        trigger = "api" if row.operation == "application_deploy" else "rollback"
        status = "deactivated" if row.operation == "application_deploy" else "live"
        return _render_evidence(
            cast(str, row.provider_resource_id),
            cast(str, commit),
            trigger,
            f"provider-request-{row.delivery_id}",
            status=status,
        )
    kind = row.release_kind
    pull = 17 if kind == "ontology" else 18
    head = ONTOLOGY_HEAD if kind == "ontology" else PIPELINE_HEAD
    merge = ONTOLOGY_MERGE if kind == "ontology" else PIPELINE_MERGE
    return {
        "status": "landed",
        "repositoryId": 42,
        "pullNumber": pull,
        "headSha": head,
        "mergeCommitSha": merge,
        "mergedAt": READBACK.isoformat(),
        "providerRequestId": f"provider-request-{row.delivery_id}",
    }


def _render_evidence(
    deploy_id: str,
    commit_id: str,
    trigger: str,
    request_id: str,
    *,
    status: str = "live",
) -> dict[str, object]:
    return {
        "provider": "render",
        "serviceId": "srv-foundry-lite",
        "deployId": deploy_id,
        "status": status,
        "providerStatus": status,
        "commitId": commit_id,
        "trigger": trigger,
        "finishedAt": READBACK.isoformat(),
        "isTerminal": True,
        "isSuccessful": status == "live",
        "providerRequestId": request_id,
    }


def _run_id(kind: str, tool: str) -> str:
    return f"run-{kind}-{tool}"


def _digest(value: str) -> str:
    return hash_json({"value": value})


def _subject_hash(actor_user_id: str) -> str:
    return hash_json({"issuer": ISSUER, "subject": actor_user_id})


def _authority() -> GovernedReleaseLiveAuthority:
    return GovernedReleaseLiveAuthority(
        runtime_profile="protected",
        database_backend="postgresql",
        source_provider_profile="github-release",
        deployment_provider_profile="render-infrastructure-deployment",
        source_revision="1" * 40,
        mcp_authority=GovernedReleaseMcpAuthority(
            application_id=APPLICATION,
            public_base_url="https://foundry.example.test/",
            authorization_server_issuer=f"{ISSUER}/",
            oauth_audience=RESOURCE,
            allowed_client_ids=(CLIENT,),
        ),
    )


def _reviewer_context(snapshot: ServerLoadedDatabaseSnapshot) -> RequestContext:
    return RequestContext(
        tenant_id=TENANT,
        actor_user_id="reviewer",
        request_id="request-live-attestation",
        roles=("admin",),
        application_id=APPLICATION,
        client_id=CLIENT,
        token_scopes=("osdk:connector:governed_release:execute",),
        oauth_session_hash=f"oauth-session:{snapshot.reviewer_oauth_session_hash}",
        oauth_session_authority="issuer",
        authorization_server_issuer=ISSUER,
        oauth_grant_type="authorization_code",
        oauth_resource=RESOURCE,
        is_human_oauth=True,
    )
