"""Full-path regressions for the server-owned Governed Release live collector."""

from __future__ import annotations

from base64 import b64encode
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, Literal, cast

import pytest
from foundry_lite.application.governed_release_completion_coordinates import GovernedReleaseCompletionReader
from foundry_lite.application.ports.ai_run_repository import AiExecutionRunRecord, AiToolCallRecord
from foundry_lite.application.ports.governed_release_delivery_config import GovernedReleaseDeliveryConfig
from foundry_lite.application.ports.governed_release_live_attestation_repository import (
    GovernedReleaseLiveAuthority,
    GovernedReleaseMcpAuthority,
)
from foundry_lite.application.ports.infrastructure_deployment_adapter import (
    InfrastructureDeploymentGetRequest,
    InfrastructureDeploymentObservation,
    InfrastructureDeploymentServicePolicyObservation,
    InfrastructureDeploymentServicePolicyRequest,
    InfrastructureDeploymentSourceBinding,
)
from foundry_lite.application.ports.release_delivery_repository import (
    ReleaseDeliveryOperation,
    ReleaseDeliveryRecord,
)
from foundry_lite.application.ports.runtime_repository_types import AuditEventRecord, OutboxEventRecord
from foundry_lite.application.ports.source_control_candidate import (
    PullRequestTarget,
    SourceRefSnapshot,
    SourceRepositoryRef,
)
from foundry_lite.application.ports.source_control_release import (
    SourceControlMergeReceipt,
    SourceControlMergeStatus,
)
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.services.aip.fde_tool_result import hash_json
from foundry_lite.application.services.aip.governed_release_live_attestation_service import (
    GovernedReleaseLiveAttestationService,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.ai_run_repository import SqlAlchemyAiRunRepository
from foundry_lite.infrastructure.repositories.governed_release_live_attestation_repository import (
    SqlAlchemyGovernedReleaseLiveAttestationRepository,
)
from foundry_lite.infrastructure.repositories.release_delivery_repository import (
    SqlAlchemyReleaseDeliveryRepository,
)
from foundry_lite.infrastructure.repositories.runtime_repository import SqlAlchemyRuntimeRepository
from sqlalchemy import create_engine, func, insert, select, update
from sqlalchemy.engine import Engine

TENANT = "tenant-live-full-path"
APPLICATION = "release-app"
ISSUER = "https://identity.example.test"
PUBLIC_BASE = "https://foundry.example.test"
RESOURCE = f"{PUBLIC_BASE}/mcp/release/{APPLICATION}"
CLIENT = "chatgpt-release-client"
SCOPE = "osdk:connector:governed_release:execute"
ORIGIN = "https://chatgpt.com"
START = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)
READBACK = datetime(2026, 8, 9, 10, 0, tzinfo=UTC)
REPOSITORY = SourceRepositoryRef("github", 42, "acme", "foundry-lite")
ONTOLOGY_HEAD, ONTOLOGY_TREE, ONTOLOGY_MERGE = "a" * 40, "b" * 40, "c" * 40
PIPELINE_HEAD, PIPELINE_TREE, PIPELINE_MERGE = "d" * 40, "e" * 40, "f" * 40
PRIOR_COMMIT = "7" * 40
ACTION_SEQUENCE: dict[str, tuple[str, ...]] = {
    "ontology": (
        "publish_release_candidate",
        "assign_release_reviewer",
        "submit_release_decision",
        "execute_approved_release",
        "rollback_release",
    ),
    "pipeline": (
        "publish_release_candidate",
        "assign_release_reviewer",
        "submit_release_decision",
        "execute_approved_release",
        "deploy_release",
        "rollback_release",
    ),
}
DELIVERY_TOOL = {
    "source_publish": "publish_release_candidate",
    "source_merge": "execute_approved_release",
    "application_deploy": "deploy_release",
    "application_rollback": "rollback_release",
}


def test_real_collector_full_path_commits_once_and_crash_replay_skips_providers() -> None:
    harness = _harness()

    created = _collect(harness)
    calls_after_commit = (harness.source.calls, harness.infrastructure.get_calls, harness.infrastructure.policy_calls)
    harness.source.should_fail = True
    harness.infrastructure.should_fail = True
    replayed = _collect(harness)

    assert created["status"] == "live_verified"
    assert created["isCreated"] is True
    assert replayed["attestationId"] == created["attestationId"]
    assert replayed["isCreated"] is False
    assert (harness.source.calls, harness.infrastructure.get_calls, harness.infrastructure.policy_calls) == (
        calls_after_commit
    )
    assert _count(harness.engine, db.governed_release_live_attestations) == 1
    assert _event_count(harness.engine, db.audit_events, "governed_release.live_attestation.verified") == 1
    assert _event_count(harness.engine, db.outbox_events, "governed_release.live_attestation.verified") == 1


def test_completion_coordinates_are_server_resolved_for_the_exact_oauth_reviewer() -> None:
    harness = _harness()
    reader = _completion_reader(harness.service)

    coordinates = reader.completion_coordinates(harness.context, APPLICATION)
    wrong_reviewer = replace(
        harness.context,
        actor_user_id="different-reviewer",
        oauth_session_hash=f"oauth-session:{_digest('different-reviewer-session')}",
    )
    rejected = reader.completion_coordinates(wrong_reviewer, APPLICATION)

    assert coordinates == {
        "attestationPurpose": "rollback_rehearsal",
        "ontologyWorkflowRunId": harness.ontology_workflow_run_id,
        "pipelineWorkflowRunId": harness.pipeline_workflow_run_id,
        "isEligible": True,
        "nextAction": "verify_release_completion",
        "reason": None,
    }
    assert rejected["isEligible"] is False
    assert rejected["ontologyWorkflowRunId"] is None
    assert rejected["pipelineWorkflowRunId"] is None
    assert rejected["nextAction"] is None


def test_real_collector_rejects_provider_drift_without_attestation() -> None:
    harness = _harness(is_provider_drift=True)

    with pytest.raises(ConflictDetected) as exc_info:
        _collect(harness)

    blockers = cast(tuple[str, ...] | list[str], exc_info.value.details.get("blockers", ()))
    assert "provider_evidence_changed_during_collection" in blockers
    assert _count(harness.engine, db.governed_release_live_attestations) == 0


def test_real_collector_rejects_final_database_drift_without_attestation() -> None:
    harness = _harness(is_database_drift=True)

    with pytest.raises(ConflictDetected) as exc_info:
        _collect(harness)

    blockers = cast(tuple[str, ...] | list[str], exc_info.value.details.get("blockers", ()))
    assert "database_snapshot_changed_during_collection" in blockers
    assert _count(harness.engine, db.governed_release_live_attestations) == 0


def test_real_collector_rolls_back_attestation_audit_and_outbox_together() -> None:
    harness = _harness(should_fail_evidence=True)

    with pytest.raises(RuntimeError, match="simulated outbox crash"):
        _collect(harness)

    assert _count(harness.engine, db.governed_release_live_attestations) == 0
    assert _event_count(harness.engine, db.audit_events, "governed_release.live_attestation.verified") == 0
    assert _event_count(harness.engine, db.outbox_events, "governed_release.live_attestation.verified") == 0


@dataclass(frozen=True, slots=True)
class _Action:
    release_kind: str
    tool_name: str
    proposal_id: str
    run_id: str
    actor: str
    session_id: str
    idempotency_key: str
    started_at: datetime
    completed_at: datetime
    binding_hash: str
    arguments_hash: str
    request_id: str
    result: dict[str, object]


@dataclass(slots=True)
class _Harness:
    engine: Engine
    service: GovernedReleaseLiveAttestationService
    context: RequestContext
    source: _SourceControl
    infrastructure: _Infrastructure
    ontology_workflow_run_id: str
    pipeline_workflow_run_id: str


def _harness(
    *,
    is_provider_drift: bool = False,
    is_database_drift: bool = False,
    should_fail_evidence: bool = False,
) -> _Harness:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    ai_runs = SqlAlchemyAiRunRepository(engine)
    runtime = SqlAlchemyRuntimeRepository(engine)
    actions = _actions()
    deliveries = _deliveries(actions)
    _seed(engine, ai_runs, runtime, actions, deliveries)
    source = _SourceControl()
    infrastructure = _Infrastructure(
        engine=engine,
        is_provider_drift=is_provider_drift,
        is_database_drift=is_database_drift,
    )
    authority = GovernedReleaseLiveAuthority(
        runtime_profile="protected",
        database_backend="postgresql",
        source_provider_profile="github-release",
        deployment_provider_profile="render-infrastructure-deployment",
        source_provider_name="github",
        deployment_provider_name="render",
        is_source_provider_live=True,
        is_deployment_provider_live=True,
        source_revision="1" * 40,
        mcp_authority=GovernedReleaseMcpAuthority(
            application_id=APPLICATION,
            public_base_url=PUBLIC_BASE,
            authorization_server_issuer=ISSUER,
            oauth_audience=RESOURCE,
            allowed_client_ids=(CLIENT,),
        ),
    )
    service = GovernedReleaseLiveAttestationService(
        ai_run_repository=ai_runs,
        engine=engine,
        governed_release_delivery_config=_config(),
        governed_release_live_attestation_repository=SqlAlchemyGovernedReleaseLiveAttestationRepository(engine),
        governed_release_live_authority=authority,
        infrastructure_deployment_adapter=infrastructure,
        release_delivery_repository=SqlAlchemyReleaseDeliveryRepository(engine),
        runtime_repository=runtime,
        source_control_release_adapter=source,
    )
    service.bind_collaborators({"runtime_service": _DurableEvidenceBoundary(runtime, should_fail=should_fail_evidence)})
    context = _reviewer_context(actions)
    roots = {
        kind: next(
            action.run_id
            for action in actions
            if action.release_kind == kind and action.tool_name == "publish_release_candidate"
        )
        for kind in ACTION_SEQUENCE
    }
    return _Harness(engine, service, context, source, infrastructure, roots["ontology"], roots["pipeline"])


def _collect(harness: _Harness) -> dict[str, object]:
    return harness.service.collect_and_store_server_verified(
        harness.context,
        APPLICATION,
        harness.ontology_workflow_run_id,
        harness.pipeline_workflow_run_id,
    )


def _completion_reader(service: GovernedReleaseLiveAttestationService) -> GovernedReleaseCompletionReader:
    return GovernedReleaseCompletionReader(
        ai_run_repository=service.ai_run_repository,
        engine=service.engine,
        governed_release_live_authority=service.governed_release_live_authority,
        release_delivery_repository=service.release_delivery_repository,
        runtime_repository=service.runtime_repository,
    )


def _actions() -> tuple[_Action, ...]:
    values: list[_Action] = []
    for kind_index, (kind, tools) in enumerate(ACTION_SEQUENCE.items()):
        for tool_index, tool in enumerate(tools):
            actor = "submitter" if tool == "publish_release_candidate" else "reviewer"
            started = START - timedelta(hours=2) + timedelta(minutes=(kind_index * 40) + tool_index * 5)
            run_id = f"run-{kind}-{tool}"
            arguments_hash = hash_json({"releaseKind": kind, "proposalId": f"{kind}-proposal", "tool": tool})
            session_hash = _digest(f"{actor}-session")
            session_id = f"mcp-{actor}-{kind}"
            idempotency_key = f"idempotency-{kind}-{tool}"
            payload = _binding_payload(
                kind,
                tool,
                actor,
                session_hash,
                session_id,
                idempotency_key,
                arguments_hash,
                started,
            )
            values.append(
                _Action(
                    kind,
                    tool,
                    f"{kind}-proposal",
                    run_id,
                    actor,
                    session_id,
                    idempotency_key,
                    started,
                    started + timedelta(minutes=4),
                    hash_json(payload),
                    arguments_hash,
                    f"request-{run_id}",
                    {"release": _release_result(kind, tool)},
                )
            )
    return tuple(values)


def _binding_payload(
    kind: str,
    tool: str,
    actor: str,
    session_hash: str,
    session_id: str,
    idempotency_key: str,
    arguments_hash: str,
    started_at: datetime,
) -> dict[str, object]:
    return {
        "tenantId": TENANT,
        "actorUserId": actor,
        "applicationId": APPLICATION,
        "clientId": CLIENT,
        "oauthSessionHash": f"oauth-session:{session_hash}",
        "oauthSessionAuthority": "issuer",
        "authorizationServerIssuer": ISSUER,
        "oauthGrantType": "authorization_code",
        "oauthResource": RESOURCE,
        "isHuman": True,
        "requiredScope": SCOPE,
        "oauthTokenIssuedAt": int(started_at.timestamp()) - 60,
        "oauthTokenExpiresAt": int(started_at.timestamp()) + 3600,
        "sessionId": session_id,
        "toolName": tool,
        "argumentsHash": arguments_hash,
        "idempotencyKey": idempotency_key,
        "requiredPermission": _permission(kind, tool),
        "origin": ORIGIN,
        "releaseKind": kind,
        "proposalId": f"{kind}-proposal",
    }


def _release_result(kind: str, tool: str) -> dict[str, object]:
    release: dict[str, object] = {
        "releaseKind": kind,
        "proposalId": f"{kind}-proposal",
        "stage": "awaiting_review",
    }
    if tool == "submit_release_decision":
        fingerprint = _digest(f"{kind}-proposal-content")
        candidate_key = "fingerprint" if kind == "ontology" else "graphFingerprint"
        candidate_value = fingerprint if kind == "ontology" else fingerprint.removeprefix("sha256:")
        release.update(
            {
                "stage": "approved",
                "candidate": {candidate_key: candidate_value},
                "releaseEvidence": {
                    "validationEvidence": [
                        {"status": "passed", "proofKind": "durable_candidate_validation"},
                        {"status": "passed", "proofKind": "source_control_merge_result_or_head_required_checks"},
                    ]
                },
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


def _deliveries(actions: tuple[_Action, ...]) -> tuple[ReleaseDeliveryRecord, ...]:
    action_map = {(action.release_kind, action.tool_name): action for action in actions}
    ontology_publish = _source_publish("ontology", 17, ONTOLOGY_HEAD, ONTOLOGY_TREE, action_map)
    ontology_merge = _source_merge(ontology_publish, ONTOLOGY_MERGE, action_map)
    pipeline_publish = _source_publish("pipeline", 18, PIPELINE_HEAD, PIPELINE_TREE, action_map)
    pipeline_merge = _source_merge(pipeline_publish, PIPELINE_MERGE, action_map)
    deploy = _application_delivery(pipeline_merge, "application_deploy", "dep-current", PIPELINE_MERGE, action_map)
    rollback = _application_delivery(deploy, "application_rollback", "dep-rollback", PRIOR_COMMIT, action_map)
    return ontology_publish, ontology_merge, pipeline_publish, pipeline_merge, deploy, rollback


def _source_publish(
    kind: str,
    pull_number: int,
    head_sha: str,
    tree_sha: str,
    actions: dict[tuple[str, str], _Action],
) -> ReleaseDeliveryRecord:
    proposal = f"{kind}-proposal"
    action = actions[(kind, "publish_release_candidate")]
    target = {
        "repositoryId": REPOSITORY.repository_id,
        "repositoryOwner": REPOSITORY.owner,
        "repositoryName": REPOSITORY.name,
        "baseRef": "main",
        "headRef": f"codex/{proposal}",
        "baseSha": "8" * 40,
    }
    canonical_bytes = f'{{"proposalId":"{proposal}","releaseKind":"{kind}"}}'.encode()
    candidate = {
        "releaseKind": kind,
        "proposalId": proposal,
        "artifactPath": f".foundry-lite/releases/{kind}/{proposal}.json",
        "manifestFingerprint": f"sha256:{sha256(canonical_bytes).hexdigest()}",
        "manifestCanonicalBytesBase64": b64encode(canonical_bytes).decode("ascii"),
        "proposalContentFingerprint": _digest(f"{kind}-proposal-content"),
    }
    result = {
        "status": "published",
        **target,
        "manifestFingerprint": candidate["manifestFingerprint"],
        "headSha": head_sha,
        "treeSha": tree_sha,
        "pullNumber": pull_number,
    }
    return _delivery(kind, "source_publish", action, None, f"pull:{pull_number}", target, candidate, result)


def _source_merge(
    publication: ReleaseDeliveryRecord,
    merge_sha: str,
    actions: dict[tuple[str, str], _Action],
) -> ReleaseDeliveryRecord:
    published = cast(dict[str, object], publication.result_ref)
    candidate = cast(dict[str, object], publication.candidate_ref)
    action = actions[(publication.release_kind, "execute_approved_release")]
    return _delivery(
        publication.release_kind,
        "source_merge",
        action,
        publication,
        publication.provider_resource_id,
        {
            "repositoryId": REPOSITORY.repository_id,
            "repositoryOwner": REPOSITORY.owner,
            "repositoryName": REPOSITORY.name,
            "pullNumber": published["pullNumber"],
            "baseRef": "main",
            "headSha": published["headSha"],
        },
        {
            "baseSha": publication.target_ref["baseSha"],
            "headRef": publication.target_ref["headRef"],
            "candidateTreeSha": published["treeSha"],
            "manifestArtifactPath": candidate["artifactPath"],
            "manifestFingerprint": candidate["manifestFingerprint"],
            "isReadyToMerge": True,
            "requiredChecks": [{"context": "quality", "isSuccessful": True}],
        },
        {"mergeCommitSha": merge_sha},
    )


def _application_delivery(
    parent: ReleaseDeliveryRecord,
    operation: ReleaseDeliveryOperation,
    deploy_id: str,
    commit_id: str,
    actions: dict[tuple[str, str], _Action],
) -> ReleaseDeliveryRecord:
    tool = "deploy_release" if operation == "application_deploy" else "rollback_release"
    candidate = (
        {"commitId": commit_id}
        if operation == "application_deploy"
        else {"targetDeployId": "dep-prior", "targetCommitId": commit_id}
    )
    result = _render_evidence(
        deploy_id,
        commit_id,
        "api" if operation == "application_deploy" else "rollback",
        f"historical-{deploy_id}",
        status="live",
    )
    return _delivery(
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


def _delivery(
    kind: str,
    operation: ReleaseDeliveryOperation,
    action: _Action,
    parent: ReleaseDeliveryRecord | None,
    provider_resource_id: str | None,
    target: Mapping[str, object],
    candidate: Mapping[str, object],
    result: Mapping[str, object],
    *,
    prior_resource_id: str | None = None,
) -> ReleaseDeliveryRecord:
    delivery_id = f"delivery-{kind}-{operation}"
    return ReleaseDeliveryRecord(
        delivery_id=delivery_id,
        tenant_id=TENANT,
        application_id=APPLICATION,
        proposal_id=f"{kind}-proposal",
        release_kind=cast(Literal["ontology", "pipeline"], kind),
        workflow_run_id=action.run_id if parent is None else parent.workflow_run_id,
        parent_delivery_id=parent.delivery_id if parent else None,
        provider="github" if operation.startswith("source_") else "render",
        operation=operation,
        status="landed",
        target_ref=dict(target),
        candidate_ref=dict(candidate),
        environment="main" if operation.startswith("source_") else "production",
        idempotency_key=f"key-{delivery_id}",
        request_fingerprint=_digest(f"request-{delivery_id}"),
        provider_operation_id=f"operation-{delivery_id}",
        provider_resource_id=provider_resource_id,
        prior_resource_id=prior_resource_id,
        result_ref=dict(result),
        error_ref=None,
        ai_run_id=action.run_id,
        binding_hash=action.binding_hash,
        execution_attempt=1,
        request_id=action.request_id,
        created_by=action.actor,
        created_at=action.started_at.isoformat(),
        updated_at=(action.started_at + timedelta(minutes=2)).isoformat(),
        dispatch_started_at=(action.started_at + timedelta(minutes=1)).isoformat(),
        completed_at=(action.started_at + timedelta(minutes=2)).isoformat(),
    )


def _seed(
    engine: Engine,
    ai_runs: SqlAlchemyAiRunRepository,
    runtime: SqlAlchemyRuntimeRepository,
    actions: tuple[_Action, ...],
    deliveries: tuple[ReleaseDeliveryRecord, ...],
) -> None:
    with engine.begin() as transaction:
        for action in actions:
            payload = _binding_payload(
                action.release_kind,
                action.tool_name,
                action.actor,
                _digest(f"{action.actor}-session"),
                action.session_id,
                action.idempotency_key,
                action.arguments_hash,
                action.started_at,
            )
            ai_runs.create_execution_run(
                transaction=transaction,
                record=_run_record(action, payload),
            )
            ai_runs.record_tool_call(transaction=transaction, record=_tool_record(action))
            runtime.insert_audit_event(transaction=transaction, record=_action_audit(action))
        for row in deliveries:
            transaction.execute(insert(db.governed_release_deliveries).values(**_delivery_values(row)))


def _run_record(action: _Action, payload: dict[str, object]) -> AiExecutionRunRecord:
    return AiExecutionRunRecord(
        id=action.run_id,
        tenant_id=TENANT,
        session_id=action.session_id,
        agent_version_id=f"governed-release-mcp:{APPLICATION}:v1",
        actor_user_id=action.actor,
        request_id=action.request_id,
        trace_id=action.request_id,
        status="succeeded",
        ontology_version_id="active-ontology",
        model_alias_version="none",
        resolved_model_id="none",
        resolved_model_revision="none",
        prompt_version_id="governed-release-mcp-v1",
        compiled_prompt_hash=action.binding_hash,
        tool_manifest_hash=hash_json([action.tool_name]),
        context_manifest_hash=hash_json([APPLICATION, action.session_id]),
        state_snapshot_hash=action.binding_hash,
        policy_snapshot_hash=hash_json({"source": "governed_release_mcp"}),
        budget_json={
            "kind": "governed_release_mcp_action",
            "requestBinding": payload,
            "requestBindingHash": action.binding_hash,
            "maxToolCalls": 1,
            "maxModelCalls": 0,
        },
        usage_json={"source": "governed_release_mcp"},
        error_json=None,
        started_at=action.started_at.isoformat(),
        completed_at=action.completed_at.isoformat(),
    )


def _tool_record(action: _Action) -> AiToolCallRecord:
    started = action.started_at + timedelta(minutes=1)
    completed = action.completed_at - timedelta(minutes=1)
    return AiToolCallRecord(
        id=f"{action.run_id}-tool-1",
        tenant_id=TENANT,
        ai_run_id=action.run_id,
        sequence=1,
        tool_id=action.tool_name,
        tool_version="v1",
        arguments_hash=action.arguments_hash,
        effect="WRITE",
        authorization_decision="allowed_by_widget_human_oauth_confirmation",
        confirmation_policy="USER",
        status="succeeded",
        result_hash=hash_json(action.result),
        linked_action_run_id=None,
        started_at=started.isoformat(),
        completed_at=completed.isoformat(),
        error_json=None,
        result_json=action.result,
    )


def _action_audit(action: _Action) -> AuditEventRecord:
    return AuditEventRecord(
        event_id=f"audit-{action.release_kind}-{action.tool_name}",
        tenant_id=TENANT,
        actor_user_id=action.actor,
        event_type="governed_release.action.succeeded",
        resource_type="governed_release_proposal",
        resource_id=action.proposal_id,
        action=action.tool_name,
        decision="allow",
        policy_decision={},
        before_ref={},
        after_ref={
            "toolName": action.tool_name,
            "releaseKind": action.release_kind,
            "status": "succeeded",
        },
        correlation_id=action.run_id,
        request_id=action.request_id,
        metadata={},
        created_at=(action.completed_at + timedelta(minutes=1)).isoformat(),
    )


def _delivery_values(row: ReleaseDeliveryRecord) -> dict[str, object]:
    values = asdict(row)
    values["id"] = values.pop("delivery_id")
    return values


class _SourceControl:
    profile_name = "github-release"
    provider_name = "github"
    is_live_provider = True

    def __init__(self) -> None:
        self.calls = 0
        self.should_fail = False

    def lookup_merge(self, target: PullRequestTarget) -> SourceControlMergeReceipt:
        if self.should_fail:
            raise AssertionError("provider must not be called during committed replay")
        self.calls += 1
        merge_sha = ONTOLOGY_MERGE if target.pull_number == 17 else PIPELINE_MERGE
        return SourceControlMergeReceipt(
            status=SourceControlMergeStatus.LANDED,
            repository_id=target.repository.repository_id,
            pull_number=target.pull_number,
            head_sha=target.expected_head_sha,
            merge_commit_sha=merge_sha,
            merged_at=READBACK.isoformat(),
            provider_request_id=f"github-read-{self.calls}",
        )

    def inspect_source_ref(self, repository: SourceRepositoryRef, ref: str) -> SourceRefSnapshot:
        if self.should_fail:
            raise AssertionError("provider must not be called during committed replay")
        return SourceRefSnapshot(repository, ref, "9" * 40, "6" * 40)


class _Infrastructure:
    profile_name = "render-infrastructure-deployment"
    provider_name = "render"
    is_live_provider = True

    def __init__(self, *, engine: Engine, is_provider_drift: bool, is_database_drift: bool) -> None:
        self.engine = engine
        self.is_provider_drift = is_provider_drift
        self.is_database_drift = is_database_drift
        self.get_calls = 0
        self.policy_calls = 0
        self.should_fail = False

    def get(self, request: InfrastructureDeploymentGetRequest) -> InfrastructureDeploymentObservation:
        if self.should_fail:
            raise AssertionError("provider must not be called during committed replay")
        self.get_calls += 1
        is_rollback = request.deploy_id == "dep-rollback"
        status = "live" if is_rollback else "deactivated"
        finished_at = READBACK + timedelta(minutes=1) if self.is_provider_drift and self.get_calls == 4 else READBACK
        return InfrastructureDeploymentObservation(
            provider="render",
            service_id=request.service_id,
            deploy_id=request.deploy_id,
            status=cast(Any, status),
            provider_status=status,
            commit_id=PRIOR_COMMIT if is_rollback else PIPELINE_MERGE,
            trigger="rollback" if is_rollback else "api",
            created_at=READBACK - timedelta(minutes=4),
            started_at=READBACK - timedelta(minutes=3),
            updated_at=READBACK - timedelta(minutes=1),
            finished_at=finished_at,
            is_terminal=True,
            is_successful=status == "live",
            provider_request_id=f"render-read-{self.get_calls}",
        )

    def get_service_policy(
        self,
        request: InfrastructureDeploymentServicePolicyRequest,
    ) -> InfrastructureDeploymentServicePolicyObservation:
        if self.should_fail:
            raise AssertionError("provider must not be called during committed replay")
        self.policy_calls += 1
        if self.is_database_drift and self.policy_calls == 2:
            with self.engine.begin() as transaction:
                transaction.execute(
                    update(db.governed_release_deliveries)
                    .where(db.governed_release_deliveries.c.id == "delivery-pipeline-application_rollback")
                    .values(updated_at=(READBACK + timedelta(minutes=30)).isoformat())
                )
        return InfrastructureDeploymentServicePolicyObservation(
            provider="render",
            service_id=request.service_id,
            release_mode="source_revision",
            trigger_mode="manual",
            source_binding=InfrastructureDeploymentSourceBinding(
                "github",
                REPOSITORY.owner,
                REPOSITORY.name,
                "main",
            ),
            workload_kind="web_service",
            is_suspended=False,
            provider_request_id=f"render-policy-{self.policy_calls}",
        )


class _DurableEvidenceBoundary:
    def __init__(self, runtime: SqlAlchemyRuntimeRepository, *, should_fail: bool) -> None:
        self.runtime = runtime
        self.should_fail = should_fail

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
        policy_decision: dict[str, object] | None = None,
        before_ref: dict[str, object] | None = None,
        after_ref: dict[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self.runtime.insert_audit_event(
            transaction=conn,
            record=AuditEventRecord(
                event_id=f"live-audit-{resource_id}",
                tenant_id=ctx.tenant_id,
                actor_user_id=ctx.actor_user_id,
                event_type=event_type,
                resource_type=resource_type,
                resource_id=resource_id,
                action=action,
                decision=decision,
                policy_decision=policy_decision or {},
                before_ref=before_ref or {},
                after_ref=after_ref or {},
                correlation_id=correlation_id or ctx.request_id,
                request_id=ctx.request_id,
                metadata={},
                created_at=datetime.now(UTC).isoformat(),
            ),
        )

    def _outbox(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, object],
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> str:
        event_id = f"live-outbox-{aggregate_id}"
        self.runtime.insert_outbox_event(
            transaction=conn,
            record=OutboxEventRecord(
                event_id=event_id,
                tenant_id=ctx.tenant_id,
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                payload=payload,
                status="pending",
                attempts=0,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
                created_at=datetime.now(UTC).isoformat(),
                published_at=None,
            ),
        )
        if self.should_fail:
            raise RuntimeError("simulated outbox crash")
        return event_id


def _reviewer_context(actions: tuple[_Action, ...]) -> RequestContext:
    reviewer = next(action for action in actions if action.actor == "reviewer")
    now = int(datetime.now(UTC).timestamp())
    return RequestContext(
        tenant_id=TENANT,
        actor_user_id="reviewer",
        request_id="request-live-full-path",
        roles=("admin",),
        application_id=APPLICATION,
        client_id=CLIENT,
        token_scopes=(SCOPE,),
        oauth_session_id="issuer-session-reviewer",
        oauth_session_hash=f"oauth-session:{_digest('reviewer-session')}",
        oauth_session_authority="issuer",
        authorization_server_issuer=ISSUER,
        oauth_grant_type="authorization_code",
        oauth_resource=RESOURCE,
        oauth_token_issued_at=now - 60,
        oauth_token_expires_at=now + 3600,
        is_human_oauth=True,
        governed_release_run_id="governed_release_run_verify_full_path",
        governed_release_binding_hash=hash_json({"action": "verify_release_completion"}),
        governed_release_session_id=reviewer.session_id,
    )


def _config() -> GovernedReleaseDeliveryConfig:
    return GovernedReleaseDeliveryConfig(
        source_repository=REPOSITORY,
        source_base_ref="main",
        is_source_control_required=True,
        deployment_service_id="srv-foundry-lite",
        deployment_environment="production",
        is_deployment_required=True,
    )


def _permission(kind: str, tool: str) -> str:
    if tool == "publish_release_candidate":
        return "pipeline:write" if kind == "pipeline" else "ontology:validate"
    if tool in {"assign_release_reviewer", "submit_release_decision"}:
        return "pipeline:review" if kind == "pipeline" else "ontology:activate"
    return "pipeline:deploy" if kind == "pipeline" else "ontology:activate"


def _render_evidence(
    deploy_id: str,
    commit_id: str,
    trigger: str,
    request_id: str,
    *,
    status: str,
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


def _count(engine: Engine, table: Any) -> int:
    with engine.begin() as transaction:
        return int(transaction.execute(select(func.count()).select_from(table)).scalar_one())


def _event_count(engine: Engine, table: Any, event_type: str) -> int:
    with engine.begin() as transaction:
        statement = select(func.count()).select_from(table).where(table.c.event_type == event_type)
        return int(transaction.execute(statement).scalar_one())


def _digest(value: str) -> str:
    return hash_json({"value": value})
