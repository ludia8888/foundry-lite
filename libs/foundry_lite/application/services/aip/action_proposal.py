"""AIP Action Proposal Service (P0g, §8.10/§12.2).

The model may propose an ontology action, but it may not execute it. This
service turns a model proposal into a durable human-review row only after the
server re-checks the AI run ledger, agent action allowlist, ontology action
definition, caller policy, and current target object version.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from foundry_lite.application.action_types import ActionExecutionPlanResponse
from foundry_lite.application.ports import ActionTypeRow, ObjectRecordRow, TransactionContext
from foundry_lite.application.ports.ai_run_repository import AiLedgerRow
from foundry_lite.application.services.action_plan_payloads import action_plan_object_versions
from foundry_lite.application.services.action_planning_service import ActionPlanningService
from foundry_lite.application.services.action_protocols import ActionOntologyLookup
from foundry_lite.application.services.aip.action_proposal_external import (
    external_mcp_proposal,
    external_mcp_replay,
    external_mcp_result,
    external_mcp_status,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.insight_review_payloads import InsightReviewProposalFields
from foundry_lite.application.services.insight_review_service import InsightReviewService
from foundry_lite.application.services.object_store.records import ObjectRecordsService
from foundry_lite.application.services.object_store.serving_contract import record_scope_object_type_id
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, PermissionDenied

JsonObject = Mapping[str, object]
_PROPOSAL_TYPE = "ontology_action"


@dataclass(frozen=True)
class ActionProposalRequest:
    """One model-proposed ontology action awaiting human review."""

    originating_ai_run_id: str
    action_type: str
    target_object_type: str
    target_object_id: str
    expected_object_version: int
    parameters: JsonObject
    evidence_context_ids: tuple[str, ...]
    agent_allowed_actions: tuple[str, ...]
    policy_version: str
    expires_at: str
    claim_text: str
    originating_tool_call_id: str | None = None
    priority: str = "normal"
    assignee_user_id: str | None = None


@dataclass(frozen=True)
class ActionProposalResult:
    """Created review plus canonical proposal fingerprint."""

    proposal_id: str
    proposal_fingerprint: str
    review_id: str
    review_payload: Mapping[str, object]
    action_proposal: Mapping[str, object]


@dataclass
class ActionProposalError(Exception):
    """Typed fail-closed action proposal rejection."""

    reason: str
    detail: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.detail)


class ActionProposalService(CoreService):
    """Create human-review action proposals without calling ActionService."""

    required_dependencies = ("engine", "policy", "ai_run_repository")
    required_collaborators = (
        "action_planning_service",
        "insight_review_service",
        "ontology_service",
        "object_records_service",
    )
    action_planning_service: ActionPlanningService
    insight_review_service: InsightReviewService
    object_records_service: ObjectRecordsService
    # Narrow ontology boundary (not the concrete OntologyService) so aip
    # modules never import the ontology service graph — that edge would close
    # an import cycle through the activation service's logic validation.
    ontology_service: ActionOntologyLookup

    def propose(self, ctx: RequestContext, request: ActionProposalRequest) -> ActionProposalResult:
        _validate_request(request)
        self._require_policy(ctx, request)
        execution_plan = self._execution_plan(ctx, request)
        with self.engine.begin() as transaction:
            ledger = self.ai_run_repository.ledger_for_run(
                transaction=transaction, tenant_id=ctx.tenant_id, ai_run_id=request.originating_ai_run_id
            )
            if ledger is None:
                raise ActionProposalError("run_not_found", f"AI run {request.originating_ai_run_id} was not found")
            action = self._action_type(transaction, ctx, request)
            self._target_record(transaction, ctx, action, request)
            evidence_refs = _evidence_refs(request.evidence_context_ids, ledger["contextItems"])
            fingerprint = compute_action_proposal_fingerprint(
                action_type=request.action_type,
                target_object_type=request.target_object_type,
                target_object_id=request.target_object_id,
                expected_object_version=request.expected_object_version,
                parameters=request.parameters,
                evidence_refs=evidence_refs,
                agent_version_id=_required_str(ledger["run"], "agent_version_id"),
                policy_version=request.policy_version,
                plan_hash=execution_plan["planHash"],
                action_version=execution_plan["ontologyVersionId"],
                object_versions=action_plan_object_versions(execution_plan),
            )
        proposal = _action_proposal_payload(request, evidence_refs, fingerprint, execution_plan)
        review = self._create_review(ctx, request, evidence_refs, fingerprint, proposal)
        return ActionProposalResult(
            proposal_id=str(proposal["proposalId"]),
            proposal_fingerprint=fingerprint,
            review_id=str(review["id"]),
            review_payload=review,
            action_proposal=proposal,
        )

    def propose_external_mcp(
        self,
        ctx: RequestContext,
        *,
        application_id: str,
        session_id: str,
        json_rpc_id: str,
        action_type: str,
        target_object_type: str,
        target_object_id: str,
        expected_object_version: int,
        parameters: Mapping[str, object],
        execution_plan: ActionExecutionPlanResponse,
        idempotency_key: str,
    ) -> dict[str, object]:
        target = (target_object_type, target_object_id, expected_object_version)
        request = _external_mcp_request(application_id, session_id, json_rpc_id, action_type, *target, parameters)
        self._require_policy(ctx, request)
        evidence_refs = _external_mcp_evidence(ctx, request, application_id, session_id, json_rpc_id)
        fingerprint = _external_mcp_fingerprint(request, execution_plan, evidence_refs, application_id)
        existing = self.insight_review_service.replay_created_review(idempotency_key, ctx=ctx)
        if existing is not None:
            return external_mcp_replay(existing, execution_plan, fingerprint)
        proposal = external_mcp_proposal(
            _action_proposal_payload(request, evidence_refs, fingerprint, execution_plan),
            application_id,
            ctx.client_id,
            ctx.token_scopes,
        )
        review = self._create_review(
            ctx, request, evidence_refs, fingerprint, proposal, idempotency_key=idempotency_key
        )
        return external_mcp_result(proposal, review, execution_plan, fingerprint, request.expires_at)

    def external_mcp_status(self, ctx: RequestContext, *, application_id: str, review_id: str) -> dict[str, object]:
        review = self.insight_review_service.review_detail(review_id, ctx=ctx)
        return external_mcp_status(review, application_id, ctx.actor_user_id)

    def _execution_plan(
        self,
        ctx: RequestContext,
        request: ActionProposalRequest,
    ) -> ActionExecutionPlanResponse:
        try:
            return self.action_planning_service.plan_action(
                request.action_type,
                object_type=request.target_object_type,
                object_id=request.target_object_id,
                expected_object_version=request.expected_object_version,
                params=request.parameters,
                ctx=ctx,
            )
        except ConflictDetected as exc:
            raise ActionProposalError("object_version_conflict", "proposal expected object version is stale") from exc
        except PermissionDenied as exc:
            raise ActionProposalError("policy_denied", "proposal edit plan is not authorized") from exc

    def _create_review(
        self,
        ctx: RequestContext,
        request: ActionProposalRequest,
        evidence_refs: Sequence[JsonObject],
        fingerprint: str,
        proposal: Mapping[str, object],
        idempotency_key: str | None = None,
    ) -> Mapping[str, object]:
        review = self.insight_review_service.create_review(
            claim_id=str(proposal["proposalId"]),
            claim_text=request.claim_text,
            evidence_object_ids=_evidence_object_ids(evidence_refs),
            evidence_refs=evidence_refs,
            idempotency_key=idempotency_key or fingerprint,
            ctx=ctx,
            priority=request.priority,
            assignee_user_id=request.assignee_user_id,
            action_proposal=proposal,
            proposal_fields=InsightReviewProposalFields(
                proposal_type=_PROPOSAL_TYPE,
                proposal_fingerprint=fingerprint,
                originating_ai_run_id=request.originating_ai_run_id,
                originating_tool_call_id=request.originating_tool_call_id,
                expires_at=request.expires_at,
                execution_status="pending_review",
                approved_action_run_id=None,
                approval_policy_version=request.policy_version,
            ),
        )
        return review

    def _require_policy(self, ctx: RequestContext, request: ActionProposalRequest) -> None:
        try:
            self.policy.require(ctx, "insight:create")
            self.policy.require(ctx, f"action:execute:{request.action_type}")
        except PermissionDenied as exc:
            raise ActionProposalError("policy_denied", f"permission denied for action {request.action_type}") from exc

    def _action_type(
        self, transaction: TransactionContext, ctx: RequestContext, request: ActionProposalRequest
    ) -> ActionTypeRow:
        try:
            action = self.ontology_service._active_action_type(transaction, ctx, request.action_type)
        except NotFound as exc:
            raise ActionProposalError("action_not_found", f"action {request.action_type} was not found") from exc
        if action["target_api_name"] != request.target_object_type:
            raise ActionProposalError("action_target_mismatch", "action target object type does not match proposal")
        return action

    def _target_record(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        _action: ActionTypeRow,
        request: ActionProposalRequest,
    ) -> ObjectRecordRow:
        object_type = self.ontology_service._active_object_type(transaction, ctx, request.target_object_type)
        record = self.object_records_service._object_record(
            transaction,
            ctx,
            request.target_object_type,
            request.target_object_id,
            record_scope_object_type_id(object_type),
        )
        if record is None:
            raise ActionProposalError("target_not_found", "proposal target object was not found")
        if record["object_version"] != request.expected_object_version:
            raise ActionProposalError("object_version_conflict", "proposal expected object version is stale")
        return record


def _validate_request(request: ActionProposalRequest) -> None:
    _required_text(request.originating_ai_run_id, "originating_ai_run_id")
    _required_text(request.action_type, "action_type")
    _required_text(request.target_object_type, "target_object_type")
    _required_text(request.target_object_id, "target_object_id")
    _required_text(request.policy_version, "policy_version")
    _required_text(request.expires_at, "expires_at")
    _required_text(request.claim_text, "claim_text")
    if request.expected_object_version <= 0:
        raise ActionProposalError("invalid_object_version", "expected object version must be positive")
    if request.action_type not in request.agent_allowed_actions:
        raise ActionProposalError("action_not_in_agent_manifest", "action is not allowlisted for this agent version")
    if not request.evidence_context_ids:
        raise ActionProposalError("missing_evidence", "action proposal requires at least one evidence context")
    if len(set(request.evidence_context_ids)) != len(request.evidence_context_ids):
        raise ActionProposalError("duplicate_evidence", "action proposal evidence context ids must be unique")


def _evidence_refs(context_ids: Sequence[str], rows: list[AiLedgerRow]) -> list[dict[str, object]]:
    by_context_id = _context_items_by_id(rows)
    evidence_refs: list[dict[str, object]] = []
    for context_id in context_ids:
        item = by_context_id.get(context_id)
        if item is None:
            raise ActionProposalError("evidence_not_in_manifest", f"context id {context_id} is not in the run manifest")
        if item.get("selected") is not True:
            raise ActionProposalError("evidence_not_selected", f"context id {context_id} was not selected")
        evidence_refs.append(_evidence_ref(item))
    return evidence_refs


def _context_items_by_id(rows: list[AiLedgerRow]) -> dict[str, AiLedgerRow]:
    return {context_id: row for row in rows if isinstance((context_id := row.get("context_id")), str)}


def _evidence_ref(item: AiLedgerRow) -> dict[str, object]:
    return {
        "contextId": _required_str(item, "context_id"),
        "contextItemId": _required_str(item, "id"),
        "sourceResourceType": _required_str(item, "source_resource_type"),
        "sourceResourceId": _required_str(item, "source_resource_id"),
        "sourceVersion": _required_str(item, "source_version"),
        "contentHash": _required_str(item, "content_hash"),
        "securityPartition": _required_str(item, "security_partition"),
    }


def compute_action_proposal_fingerprint(
    *,
    action_type: str,
    target_object_type: str,
    target_object_id: str,
    expected_object_version: int,
    parameters: Mapping[str, object],
    evidence_refs: Sequence[JsonObject],
    agent_version_id: str,
    policy_version: str,
    plan_hash: str | None = None,
    action_version: str | None = None,
    object_versions: Mapping[str, object] | None = None,
) -> str:
    payload: dict[str, object] = {
        "version": 2 if plan_hash is not None else 1,
        "proposalType": _PROPOSAL_TYPE,
        "actionType": action_type,
        "targetObjectType": target_object_type,
        "targetObjectId": target_object_id,
        "expectedObjectVersion": expected_object_version,
        "parameters": dict(parameters),
        "evidenceRefs": [dict(ref) for ref in evidence_refs],
        "agentVersionId": agent_version_id,
        "policyVersion": policy_version,
    }
    if plan_hash is not None:
        payload.update(
            {
                "planHash": plan_hash,
                "actionVersion": action_version,
                "objectVersions": dict(object_versions or {}),
            }
        )
    return _hash_json(payload)


def _action_proposal_payload(
    request: ActionProposalRequest,
    evidence_refs: Sequence[JsonObject],
    fingerprint: str,
    execution_plan: ActionExecutionPlanResponse,
) -> dict[str, object]:
    proposal_id = f"aip-proposal-{fingerprint.removeprefix('sha256:')[:24]}"
    return {
        "proposalId": proposal_id,
        "proposalType": _PROPOSAL_TYPE,
        "actionType": request.action_type,
        "actionApiName": request.action_type,
        "targetObjectType": request.target_object_type,
        "targetObjectId": request.target_object_id,
        "expectedObjectVersion": request.expected_object_version,
        "parameters": dict(request.parameters),
        "evidenceRefs": [dict(ref) for ref in evidence_refs],
        "originatingAiRunId": request.originating_ai_run_id,
        "originatingToolCallId": request.originating_tool_call_id,
        "proposalFingerprint": fingerprint,
        "planHash": execution_plan["planHash"],
        "actionVersion": execution_plan["ontologyVersionId"],
        "objectVersions": action_plan_object_versions(execution_plan),
        "risk": dict(execution_plan["risk"]),
        "policyVersion": request.policy_version,
        "expiresAt": request.expires_at,
    }


def _external_mcp_request(
    application_id: str,
    session_id: str,
    json_rpc_id: str,
    action_type: str,
    target_object_type: str,
    target_object_id: str,
    expected_object_version: int,
    parameters: Mapping[str, object],
) -> ActionProposalRequest:
    call_id = f"{application_id}:{session_id}:{json_rpc_id}"
    expires_at = (datetime.now(UTC) + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
    return ActionProposalRequest(
        originating_ai_run_id=f"ontology-mcp:{application_id}:{session_id}",
        originating_tool_call_id=call_id,
        action_type=action_type,
        target_object_type=target_object_type,
        target_object_id=target_object_id,
        expected_object_version=expected_object_version,
        parameters=parameters,
        evidence_context_ids=(call_id,),
        agent_allowed_actions=(action_type,),
        policy_version="ontology-mcp-v1",
        expires_at=expires_at,
        claim_text=f"Ontology MCP requests governed execution of {action_type}.",
    )


def _external_mcp_evidence(
    ctx: RequestContext,
    request: ActionProposalRequest,
    application_id: str,
    session_id: str,
    json_rpc_id: str,
) -> list[dict[str, object]]:
    source_id = f"{application_id}:{session_id}:{json_rpc_id}"
    content_hash = _hash_json(
        {
            "actionType": request.action_type,
            "targetObjectType": request.target_object_type,
            "targetObjectId": request.target_object_id,
            "expectedObjectVersion": request.expected_object_version,
            "parameters": dict(request.parameters),
        }
    )
    return [
        {
            "contextId": source_id,
            "contextItemId": f"ontology-mcp-call:{source_id}",
            "sourceResourceType": "ontology_mcp_call",
            "sourceResourceId": source_id,
            "sourceVersion": "2025-06-18",
            "contentHash": content_hash,
            "securityPartition": ctx.tenant_id,
        }
    ]


def _external_mcp_fingerprint(
    request: ActionProposalRequest,
    plan: ActionExecutionPlanResponse,
    evidence_refs: Sequence[JsonObject],
    application_id: str,
) -> str:
    return compute_action_proposal_fingerprint(
        action_type=request.action_type,
        target_object_type=request.target_object_type,
        target_object_id=request.target_object_id,
        expected_object_version=request.expected_object_version,
        parameters=request.parameters,
        evidence_refs=evidence_refs,
        agent_version_id=f"ontology-mcp:{application_id}:2025-06-18",
        policy_version=request.policy_version,
        plan_hash=plan["planHash"],
        action_version=plan["ontologyVersionId"],
        object_versions=action_plan_object_versions(plan),
    )


def _evidence_object_ids(evidence_refs: Sequence[JsonObject]) -> list[str]:
    result: list[str] = []
    for ref in evidence_refs:
        source_id = ref.get("sourceResourceId")
        if isinstance(source_id, str):
            result.append(source_id)
    return result


def _required_text(value: str, field: str) -> str:
    if value == "":
        raise ActionProposalError("missing_field", f"{field} is required")
    return value


def _required_str(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or value == "":
        raise ActionProposalError("invalid_manifest", f"run manifest is missing {key}")
    return value


def _hash_json(value: Mapping[str, object]) -> str:
    return f"sha256:{hashlib.sha256(_json_block(value).encode()).hexdigest()}"


def _json_block(value: Mapping[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
