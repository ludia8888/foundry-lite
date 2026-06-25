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

from foundry_lite.application.ports import ActionTypeRow, ObjectRecordRow, TransactionContext
from foundry_lite.application.ports.ai_run_repository import AiLedgerRow
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.insight_review_payloads import InsightReviewProposalFields
from foundry_lite.application.services.insight_review_service import InsightReviewService
from foundry_lite.application.services.object_store.records import ObjectRecordsService
from foundry_lite.application.services.ontology_service import OntologyService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, PermissionDenied

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
    required_collaborators = ("insight_review_service", "ontology_service", "object_records_service")
    insight_review_service: InsightReviewService
    object_records_service: ObjectRecordsService
    ontology_service: OntologyService

    def propose(self, ctx: RequestContext, request: ActionProposalRequest) -> ActionProposalResult:
        _validate_request(request)
        self._require_policy(ctx, request)
        with self.engine.begin() as transaction:
            ledger = self.ai_run_repository.ledger_for_run(
                transaction=transaction, tenant_id=ctx.tenant_id, ai_run_id=request.originating_ai_run_id
            )
            if ledger is None:
                raise ActionProposalError("run_not_found", f"AI run {request.originating_ai_run_id} was not found")
            action = self._action_type(transaction, ctx, request)
            self._target_record(transaction, ctx, action, request)
            evidence_refs = _evidence_refs(request.evidence_context_ids, ledger["contextItems"])
            fingerprint = _proposal_fingerprint(request, ledger["run"], evidence_refs)
        proposal = _action_proposal_payload(request, evidence_refs, fingerprint)
        review = self._create_review(ctx, request, evidence_refs, fingerprint, proposal)
        return ActionProposalResult(
            proposal_id=str(proposal["proposalId"]),
            proposal_fingerprint=fingerprint,
            review_id=str(review["id"]),
            review_payload=review,
            action_proposal=proposal,
        )

    def _create_review(
        self,
        ctx: RequestContext,
        request: ActionProposalRequest,
        evidence_refs: Sequence[JsonObject],
        fingerprint: str,
        proposal: Mapping[str, object],
    ) -> Mapping[str, object]:
        review = self.insight_review_service.create_review(
            claim_id=str(proposal["proposalId"]),
            claim_text=request.claim_text,
            evidence_object_ids=_evidence_object_ids(evidence_refs),
            evidence_refs=evidence_refs,
            idempotency_key=fingerprint,
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
        action: ActionTypeRow,
        request: ActionProposalRequest,
    ) -> ObjectRecordRow:
        record = self.object_records_service._object_record(
            transaction, ctx, request.target_object_type, request.target_object_id, action["target_object_type_id"]
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


def _proposal_fingerprint(request: ActionProposalRequest, run: AiLedgerRow, evidence_refs: Sequence[JsonObject]) -> str:
    return _hash_json(
        {
            "version": 1,
            "proposalType": _PROPOSAL_TYPE,
            "actionType": request.action_type,
            "targetObjectType": request.target_object_type,
            "targetObjectId": request.target_object_id,
            "expectedObjectVersion": request.expected_object_version,
            "parameters": dict(request.parameters),
            "evidenceRefs": [dict(ref) for ref in evidence_refs],
            "agentVersionId": _required_str(run, "agent_version_id"),
            "policyVersion": request.policy_version,
        }
    )


def _action_proposal_payload(
    request: ActionProposalRequest, evidence_refs: Sequence[JsonObject], fingerprint: str
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
        "policyVersion": request.policy_version,
        "expiresAt": request.expires_at,
    }


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
