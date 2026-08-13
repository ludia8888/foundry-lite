from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from foundry_lite.application.ports.ai_run_repository import AiRunLedgerSnapshot, AiRunRepository
from foundry_lite.application.ports.release_delivery_repository import (
    ReleaseDeliveryKind,
    ReleaseDeliveryOperation,
    ReleaseDeliveryRecord,
    ReleaseDeliveryRepository,
)
from foundry_lite.application.ports.runtime_repository import RuntimeRepository
from foundry_lite.application.ports.runtime_repository_types import RuntimeRow
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.services.aip.fde_tool_result import hash_json
from foundry_lite.application.services.aip.governed_release_authorization import GOVERNED_RELEASE_SCOPE
from foundry_lite.application.services.aip.governed_release_live_collection_db_loader import (
    GovernedReleaseLiveCollectionDatabaseLoader,
)
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

TENANT = "tenant-a"
APPLICATION = "application-a"
ISSUER = "https://issuer.example.test"
RESOURCE = f"https://foundry.example.test/mcp/release/{APPLICATION}"
ORIGIN = "https://chatgpt.com"
SUBMITTER = "human-submitter"
REVIEWER = "human-reviewer"
SUBMITTER_SESSION = f"oauth-session:sha256:{'a' * 64}"
REVIEWER_SESSION = f"oauth-session:sha256:{'b' * 64}"

ACTION_SEQUENCE = {
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


class _Deliveries:
    def __init__(self, chains: dict[str, tuple[ReleaseDeliveryRecord, ...]]) -> None:
        self.chains = chains
        self.calls: list[tuple[str, int]] = []

    def list_for_workflow(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        application_id: str,
        workflow_run_id: str,
        limit: int,
    ) -> tuple[ReleaseDeliveryRecord, ...]:
        del transaction, tenant_id, application_id
        self.calls.append((workflow_run_id, limit))
        return self.chains.get(workflow_run_id, ())[:limit]


class _AiRuns:
    def __init__(self, ledgers: dict[str, AiRunLedgerSnapshot]) -> None:
        self.ledgers = ledgers
        self.calls: list[str] = []

    def ledger_for_run(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        ai_run_id: str,
    ) -> AiRunLedgerSnapshot | None:
        del transaction, tenant_id
        self.calls.append(ai_run_id)
        return self.ledgers.get(ai_run_id)


class _Runtime:
    def __init__(self, audits: list[RuntimeRow]) -> None:
        self.audits = audits
        self.calls: list[tuple[tuple[tuple[str, str], ...], tuple[str, ...], int]] = []

    def audit_events_for_resources(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        resource_refs: list[tuple[str, str]],
        event_types: list[str],
        limit: int,
    ) -> list[RuntimeRow]:
        del transaction, tenant_id
        self.calls.append((tuple(resource_refs), tuple(event_types), limit))
        return self.audits[:limit]


def test_loader_builds_frozen_server_claims_and_repeatable_database_fingerprint() -> None:
    loader, deliveries, ai_runs, runtime, workflow_ids = _harness()

    first = _load(loader, workflow_ids)
    second = _load(loader, workflow_ids)

    assert len(first.actions) == 11
    assert len(first.action_results) == 11
    assert len(first.action_audits) == 11
    assert len(first.deliveries) == 6
    assert len(first.delivery_records) == 6
    assert first.initial_database_fingerprint == second.initial_database_fingerprint
    assert first.initial_database_fingerprint.startswith("sha256:")
    assert first.authorization_policy_fingerprint.startswith("sha256:")
    assert first.authorization_policy["origin"] == "https://chatgpt.com"
    assert first.action_results[0].result_json["release"]
    assert first.submitter_subject_hash != first.reviewer_subject_hash
    assert first.submitter_oauth_session_hash == f"sha256:{'a' * 64}"
    assert first.reviewer_oauth_session_hash == f"sha256:{'b' * 64}"
    assert [item.operation for item in first.deliveries] == [
        "source_publish",
        "source_merge",
        "source_publish",
        "source_merge",
        "application_deploy",
        "application_rollback",
    ]
    assert deliveries.calls[:2] == [(workflow_ids["ontology"], 3), (workflow_ids["pipeline"], 5)]
    assert len(ai_runs.calls) == 22
    assert runtime.calls[0][1:] == (("governed_release.action.succeeded",), 33)


def test_duplicate_succeeded_audit_correlation_fails_closed() -> None:
    loader, _, _, runtime, workflow_ids = _harness()
    duplicate = dict(runtime.audits[0])
    duplicate["id"] = "audit-duplicate"
    runtime.audits.append(duplicate)

    with pytest.raises(ConflictDetected) as raised:
        _load(loader, workflow_ids)

    assert raised.value.details == {"reason": "action_audit_duplicate"}


def test_selected_provider_target_drift_changes_the_database_fingerprint() -> None:
    loader, deliveries, _, _, workflow_ids = _harness()
    before = _load(loader, workflow_ids)
    pipeline = list(deliveries.chains[workflow_ids["pipeline"]])
    pipeline[0] = replace(pipeline[0], candidate_ref={"candidate": "changed-after-initial-read"})
    deliveries.chains[workflow_ids["pipeline"]] = tuple(pipeline)

    after = _load(loader, workflow_ids)

    assert before.initial_database_fingerprint != after.initial_database_fingerprint
    assert after.delivery_records[2].candidate_ref == {"candidate": "changed-after-initial-read"}


def test_detached_delivery_parent_fails_closed_before_loading_ai_runs() -> None:
    loader, deliveries, ai_runs, _, workflow_ids = _harness()
    pipeline = list(deliveries.chains[workflow_ids["pipeline"]])
    pipeline[1] = replace(pipeline[1], parent_delivery_id="wrong-parent")
    deliveries.chains[workflow_ids["pipeline"]] = tuple(pipeline)

    with pytest.raises(ConflictDetected) as raised:
        _load(loader, workflow_ids)

    assert raised.value.details == {"reason": "workflow_delivery_lineage_mismatch"}
    assert ai_runs.calls == []


def test_cross_tenant_audit_row_is_rejected_even_if_repository_returns_it() -> None:
    loader, _, _, runtime, workflow_ids = _harness()
    runtime.audits[0] = {**runtime.audits[0], "tenant_id": "tenant-other"}

    with pytest.raises(ConflictDetected) as raised:
        _load(loader, workflow_ids)

    assert raised.value.details == {"reason": "action_audit_scope_mismatch"}


def test_missing_explicit_authorization_code_metadata_is_rejected() -> None:
    loader, _, ai_runs, _, workflow_ids = _harness()
    run_id = workflow_ids["ontology"]
    ledger = ai_runs.ledgers[run_id]
    run = dict(ledger["run"])
    budget = dict(cast(dict[str, object], run["budget_json"]))
    binding = dict(cast(dict[str, object], budget["requestBinding"]))
    binding["oauthGrantType"] = ""
    budget["requestBinding"] = binding
    budget["requestBindingHash"] = hash_json(binding)
    run["budget_json"] = budget
    ai_runs.ledgers[run_id] = {**ledger, "run": run}

    with pytest.raises(ValidationFailed) as raised:
        _load(loader, workflow_ids)

    assert raised.value.details == {"reason": "action_auth_metadata_invalid"}


def test_non_chatgpt_origin_is_not_hosted_collection_evidence() -> None:
    loader, _, ai_runs, _, workflow_ids = _harness()
    run_id = workflow_ids["ontology"]
    ledger = ai_runs.ledgers[run_id]
    run = dict(ledger["run"])
    budget = dict(cast(dict[str, object], run["budget_json"]))
    binding = dict(cast(dict[str, object], budget["requestBinding"]))
    binding["origin"] = "https://example.test"
    budget["requestBinding"] = binding
    budget["requestBindingHash"] = hash_json(binding)
    run["budget_json"] = budget
    ai_runs.ledgers[run_id] = {**ledger, "run": run}

    with pytest.raises(ValidationFailed) as raised:
        _load(loader, workflow_ids)

    assert raised.value.details == {"reason": "action_auth_metadata_invalid"}


def test_more_than_one_successful_tool_call_is_ambiguous() -> None:
    loader, _, ai_runs, _, workflow_ids = _harness()
    run_id = workflow_ids["ontology"]
    ledger = ai_runs.ledgers[run_id]
    tool_calls = [*ledger["toolCalls"], dict(ledger["toolCalls"][0])]
    ai_runs.ledgers[run_id] = {**ledger, "toolCalls": tool_calls}

    with pytest.raises(ConflictDetected) as raised:
        _load(loader, workflow_ids)

    assert raised.value.details == {"reason": "action_tool_call_count_mismatch"}


def test_delivery_and_audit_must_select_the_same_ai_run() -> None:
    loader, _, _, runtime, workflow_ids = _harness()
    for index, row in enumerate(runtime.audits):
        after = cast(dict[str, object], row["after_ref"])
        if after["releaseKind"] == "pipeline" and after["toolName"] == "rollback_release":
            runtime.audits[index] = {**row, "correlation_id": "wrong-rollback-run"}

    with pytest.raises(ConflictDetected) as raised:
        _load(loader, workflow_ids)

    assert raised.value.details == {"reason": "action_audit_delivery_mismatch"}


def _harness() -> tuple[
    GovernedReleaseLiveCollectionDatabaseLoader,
    _Deliveries,
    _AiRuns,
    _Runtime,
    dict[str, str],
]:
    ledgers: dict[str, AiRunLedgerSnapshot] = {}
    audits: list[RuntimeRow] = []
    action_meta: dict[tuple[str, str], tuple[str, str, str, datetime, datetime]] = {}
    for kind, base in (("ontology", _time(0)), ("pipeline", _time(60))):
        proposal_id = f"proposal-{kind}"
        for index, tool in enumerate(ACTION_SEQUENCE[kind]):
            started = base + timedelta(minutes=index * 4)
            completed = started + timedelta(minutes=2)
            run_id = f"run-{kind}-{tool}"
            actor, oauth_session = _principal(tool)
            ledger, binding_hash = _ledger(kind, proposal_id, tool, run_id, actor, oauth_session, started, completed)
            ledgers[run_id] = ledger
            action_meta[(kind, tool)] = (run_id, binding_hash, actor, started, completed)
            audits.append(_audit(kind, proposal_id, tool, run_id, actor, completed))
    chains = _chains(action_meta)
    workflow_ids = {kind: action_meta[(kind, "publish_release_candidate")][0] for kind in ("ontology", "pipeline")}
    deliveries = _Deliveries({workflow_ids[kind]: chains[kind] for kind in workflow_ids})
    ai_runs = _AiRuns(ledgers)
    runtime = _Runtime(audits)
    loader = GovernedReleaseLiveCollectionDatabaseLoader(
        cast(ReleaseDeliveryRepository, deliveries),
        cast(AiRunRepository, ai_runs),
        cast(RuntimeRepository, runtime),
    )
    return loader, deliveries, ai_runs, runtime, workflow_ids


def _ledger(
    kind: str,
    proposal_id: str,
    tool: str,
    run_id: str,
    actor: str,
    oauth_session: str,
    started: datetime,
    completed: datetime,
) -> tuple[AiRunLedgerSnapshot, str]:
    binding = _binding(kind, proposal_id, tool, actor, oauth_session, started)
    binding_hash = hash_json(binding)
    result = {"release": {"releaseKind": kind, "proposalId": proposal_id, "stage": tool}}
    run = {
        "id": run_id,
        "tenant_id": TENANT,
        "session_id": binding["sessionId"],
        "agent_version_id": f"governed-release-mcp:{APPLICATION}:v1",
        "actor_user_id": actor,
        "request_id": f"request-{run_id}",
        "status": "succeeded",
        "budget_json": {
            "kind": "governed_release_mcp_action",
            "requestBinding": binding,
            "requestBindingHash": binding_hash,
            "maxToolCalls": 1,
            "maxModelCalls": 0,
        },
        "started_at": started.isoformat(),
        "completed_at": completed.isoformat(),
    }
    tool_row = {
        "id": f"{run_id}-tool-1",
        "tenant_id": TENANT,
        "ai_run_id": run_id,
        "sequence": 1,
        "tool_id": tool,
        "tool_version": "v1",
        "effect": "WRITE",
        "authorization_decision": "allowed_by_widget_human_oauth_confirmation",
        "confirmation_policy": "USER",
        "status": "succeeded",
        "arguments_hash": binding["argumentsHash"],
        "result_hash": hash_json(result),
        "result_json": result,
        "linked_action_run_id": None,
        "error_json": None,
        "started_at": completed.isoformat(),
        "completed_at": completed.isoformat(),
    }
    return _snapshot(run, tool_row), binding_hash


def _binding(
    kind: str,
    proposal_id: str,
    tool: str,
    actor: str,
    oauth_session: str,
    started: datetime,
) -> dict[str, object]:
    is_submitter = tool == "publish_release_candidate"
    return {
        "tenantId": TENANT,
        "actorUserId": actor,
        "applicationId": APPLICATION,
        "clientId": "chatgpt-release-client",
        "oauthSessionHash": oauth_session,
        "oauthSessionAuthority": "issuer",
        "authorizationServerIssuer": ISSUER,
        "oauthGrantType": "authorization_code",
        "oauthResource": RESOURCE,
        "isHuman": True,
        "requiredScope": GOVERNED_RELEASE_SCOPE,
        "oauthTokenIssuedAt": int(started.timestamp()) - 60,
        "oauthTokenExpiresAt": int(started.timestamp()) + 3600,
        "sessionId": f"mcp-{'submitter' if is_submitter else 'reviewer'}-{kind}",
        "toolName": tool,
        "argumentsHash": hash_json({"kind": kind, "proposalId": proposal_id, "tool": tool}),
        "idempotencyKey": f"idempotency-{kind}-{tool}",
        "requiredPermission": _permission(kind, tool),
        "origin": ORIGIN,
        "releaseKind": kind,
        "proposalId": proposal_id,
    }


def _snapshot(run: dict[str, object], tool: dict[str, object]) -> AiRunLedgerSnapshot:
    return {
        "run": run,
        "events": [],
        "modelCalls": [],
        "contextItems": [],
        "toolCalls": [tool],
        "citations": [],
        "usage": [],
        "promptArtifacts": [],
    }


def _chains(
    meta: dict[tuple[str, str], tuple[str, str, str, datetime, datetime]],
) -> dict[str, tuple[ReleaseDeliveryRecord, ...]]:
    ontology = _delivery_chain("ontology", ("source_publish", "source_merge"), meta)
    pipeline = _delivery_chain(
        "pipeline",
        ("source_publish", "source_merge", "application_deploy", "application_rollback"),
        meta,
    )
    return {"ontology": ontology, "pipeline": pipeline}


def _delivery_chain(
    kind: str,
    operations: tuple[str, ...],
    meta: dict[tuple[str, str], tuple[str, str, str, datetime, datetime]],
) -> tuple[ReleaseDeliveryRecord, ...]:
    tool_by_operation = {
        "source_publish": "publish_release_candidate",
        "source_merge": "execute_approved_release",
        "application_deploy": "deploy_release",
        "application_rollback": "rollback_release",
    }
    root = meta[(kind, "publish_release_candidate")][0]
    rows: list[ReleaseDeliveryRecord] = []
    parent: str | None = None
    for operation in operations:
        tool = tool_by_operation[operation]
        run_id, binding_hash, actor, started, _ = meta[(kind, tool)]
        row = _delivery(kind, operation, root, parent, run_id, binding_hash, actor, started)
        rows.append(row)
        parent = row.delivery_id
    return tuple(rows)


def _delivery(
    kind: str,
    operation: str,
    workflow_run_id: str,
    parent_delivery_id: str | None,
    ai_run_id: str,
    binding_hash: str,
    actor: str,
    started: datetime,
) -> ReleaseDeliveryRecord:
    delivery_id = f"delivery-{kind}-{operation}"
    provider = "github" if operation.startswith("source_") else "render"
    dispatch = started + timedelta(seconds=30)
    completed = started + timedelta(seconds=90)
    return ReleaseDeliveryRecord(
        delivery_id=delivery_id,
        tenant_id=TENANT,
        application_id=APPLICATION,
        proposal_id=f"proposal-{kind}",
        release_kind=cast(ReleaseDeliveryKind, kind),
        workflow_run_id=workflow_run_id,
        parent_delivery_id=parent_delivery_id,
        provider=provider,
        operation=cast(ReleaseDeliveryOperation, operation),
        status="landed",
        target_ref={"target": f"{kind}-{operation}"},
        candidate_ref={"candidate": f"{kind}-{operation}"},
        environment="production",
        idempotency_key=f"delivery-key-{kind}-{operation}",
        request_fingerprint=hash_json({"kind": kind, "operation": operation}),
        provider_operation_id=f"provider-operation-{delivery_id}",
        provider_resource_id=f"provider-resource-{delivery_id}",
        prior_resource_id=None,
        result_ref={"status": "landed", "operation": operation, "deliveryId": delivery_id},
        error_ref=None,
        ai_run_id=ai_run_id,
        binding_hash=binding_hash,
        execution_attempt=1,
        request_id=f"request-{ai_run_id}",
        created_by=actor,
        created_at=started.isoformat(),
        updated_at=completed.isoformat(),
        dispatch_started_at=dispatch.isoformat(),
        completed_at=completed.isoformat(),
    )


def _audit(
    kind: str,
    proposal_id: str,
    tool: str,
    run_id: str,
    actor: str,
    created_at: datetime,
) -> RuntimeRow:
    return {
        "id": f"audit-{kind}-{tool}",
        "tenant_id": TENANT,
        "actor_user_id": actor,
        "event_type": "governed_release.action.succeeded",
        "resource_type": "governed_release_proposal",
        "resource_id": proposal_id,
        "action": tool,
        "after_ref": {"toolName": tool, "releaseKind": kind, "status": "succeeded"},
        "correlation_id": run_id,
        "created_at": created_at.isoformat(),
    }


def _principal(tool: str) -> tuple[str, str]:
    return (SUBMITTER, SUBMITTER_SESSION) if tool == "publish_release_candidate" else (REVIEWER, REVIEWER_SESSION)


def _permission(kind: str, tool: str) -> str:
    if tool == "publish_release_candidate":
        return "pipeline:write" if kind == "pipeline" else "ontology:validate"
    if tool in {"assign_release_reviewer", "submit_release_decision"}:
        return "pipeline:review" if kind == "pipeline" else "ontology:activate"
    return "pipeline:deploy" if kind == "pipeline" else "ontology:activate"


def _load(loader: GovernedReleaseLiveCollectionDatabaseLoader, workflow_ids: dict[str, str]):
    return loader.load(
        transaction=object(),
        tenant_id=TENANT,
        application_id=APPLICATION,
        ontology_workflow_run_id=workflow_ids["ontology"],
        pipeline_workflow_run_id=workflow_ids["pipeline"],
    )


def _time(minutes: int) -> datetime:
    return datetime(2026, 8, 9, tzinfo=UTC) + timedelta(minutes=minutes)
