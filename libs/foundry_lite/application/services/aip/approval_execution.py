"""AIP Approval Execution Service (P0h, §8.10/§12.2).

Human approval and action execution stay separate. This service only executes
an already-approved action proposal after re-checking the proposal fingerprint,
reviewer/action permissions, source access, target object version, and write
traffic gate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.insight_review_repository import InsightReviewRow
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.aip.action_proposal import compute_action_proposal_fingerprint
from foundry_lite.application.services.aip.source_permissions import source_permission_for_type
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.insight_review_payloads import review_payload
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, PermissionDenied

JsonObject = Mapping[str, object]
_PROPOSAL_TYPE = "ontology_action"


class _ActionRunner(Protocol):
    def apply_action(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object]: ...


class _OntologyLookup(Protocol):
    def _active_action_type(
        self, transaction: TransactionContext, ctx: RequestContext, action_api_name: str
    ) -> Mapping[str, object]: ...


class _ObjectRecordLookup(Protocol):
    def _object_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        object_id: str,
        object_type_id: str | None = None,
    ) -> Mapping[str, object] | None: ...


class _RuntimeBoundary(Protocol):
    def _require_write_traffic_open(
        self,
        ctx: RequestContext,
        *,
        operation: str,
        resource_type: str,
        resource_id: str,
    ) -> None: ...

    def _run_relation(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        source_run_type: str,
        source_run_id: str,
        target_run_type: str,
        target_run_id: str,
        relation: str,
        resource_type: str,
        resource_id: str,
        metadata: Mapping[str, object] | None = None,
    ) -> bool: ...

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

    def _error_payload(
        self,
        exc: Exception,
        ctx: RequestContext | None = None,
        *,
        run_id: str | None = None,
        correlation_id: str | None = None,
        adapter: str | None = None,
    ) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class ApprovalExecutionRequest:
    review_id: str
    expected_proposal_fingerprint: str


@dataclass(frozen=True)
class ApprovalExecutionResult:
    review_id: str
    proposal_fingerprint: str
    action_run_id: str
    action_response: Mapping[str, object]
    review_payload: Mapping[str, object]


@dataclass
class ApprovalExecutionError(Exception):
    reason: str
    detail: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.detail)


@dataclass(frozen=True)
class _PreparedExecution:
    review_id: str
    proposal_fingerprint: str
    originating_ai_run_id: str
    action_type: str
    target_object_type: str
    target_object_id: str
    expected_object_version: int
    parameters: JsonObject
    policy_version: str


class ApprovalExecutionService(CoreService):
    """Execute approved ontology action proposals exactly once."""

    required_dependencies = ("engine", "policy", "ai_run_repository", "insight_review_repository")
    required_collaborators = ("action_service", "ontology_service", "object_records_service", "runtime_service")
    action_service: _ActionRunner
    object_records_service: _ObjectRecordLookup
    ontology_service: _OntologyLookup
    runtime_service: _RuntimeBoundary

    def execute(self, ctx: RequestContext, request: ApprovalExecutionRequest) -> ApprovalExecutionResult:
        _validate_request(request)
        self._require_reviewer(ctx, request.review_id)
        existing = self._existing_executed_result(ctx, request)
        if existing is not None:
            return existing
        prepared = self._prepare_execution(ctx, request)
        try:
            response = self.action_service.apply_action(
                prepared.action_type,
                object_type=prepared.target_object_type,
                object_id=prepared.target_object_id,
                expected_object_version=prepared.expected_object_version,
                params=prepared.parameters,
                idempotency_key=prepared.proposal_fingerprint,
                ctx=ctx,
            )
        except Exception as exc:
            self._mark_failed(ctx, prepared.review_id, exc)
            raise
        return self._finish_execution(ctx, prepared, response)

    def _existing_executed_result(
        self, ctx: RequestContext, request: ApprovalExecutionRequest
    ) -> ApprovalExecutionResult | None:
        with self.engine.begin() as transaction:
            row = self._review_row(transaction, ctx, request.review_id)
            if row["execution_status"] != "executed":
                return None
            _require_matching_fingerprint(row, request.expected_proposal_fingerprint)
            action_run_id = _required_row_text(row, "approved_action_run_id")
        return ApprovalExecutionResult(
            review_id=row["id"],
            proposal_fingerprint=request.expected_proposal_fingerprint,
            action_run_id=action_run_id,
            action_response={"actionRunId": action_run_id, "status": "succeeded", "idempotentReplay": True},
            review_payload=review_payload(row),
        )

    def _prepare_execution(self, ctx: RequestContext, request: ApprovalExecutionRequest) -> _PreparedExecution:
        with self.engine.begin() as transaction:
            row = self._review_row(transaction, ctx, request.review_id)
            proposal = _approved_pending_proposal(row)
            _require_matching_fingerprint(row, request.expected_proposal_fingerprint)
            prepared = _prepared_execution(row, proposal)
            self._recheck_proposal(transaction, ctx, prepared, proposal)
            self._claim_execution(transaction, ctx, prepared.review_id)
        return prepared

    def _recheck_proposal(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        prepared: _PreparedExecution,
        proposal: Mapping[str, object],
    ) -> None:
        self._require_action_permission(ctx, prepared.action_type)
        self._require_write_open(ctx, prepared.review_id)
        self._require_evidence_access(ctx, _evidence_refs(proposal))
        ledger = self._ledger(transaction, ctx, prepared.originating_ai_run_id)
        action = self._action_type(transaction, ctx, prepared)
        self._target_record(transaction, ctx, action, prepared)
        _require_not_expired(proposal)
        _require_recomputed_fingerprint(prepared, _evidence_refs(proposal), cast(JsonObject, ledger["run"]))

    def _finish_execution(
        self,
        ctx: RequestContext,
        prepared: _PreparedExecution,
        response: Mapping[str, object],
    ) -> ApprovalExecutionResult:
        action_run_id = _required_text(response, "actionRunId")
        with self.engine.begin() as transaction:
            row = self.insight_review_repository.mark_execution_succeeded(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                review_id=prepared.review_id,
                action_run_id=action_run_id,
                updated_at=_now(),
            )
            if row is None:
                raise ConflictDetected("approval execution state changed concurrently")
            self._record_execution_evidence(transaction, ctx, prepared, action_run_id, response)
        return ApprovalExecutionResult(
            review_id=prepared.review_id,
            proposal_fingerprint=prepared.proposal_fingerprint,
            action_run_id=action_run_id,
            action_response=dict(response),
            review_payload=review_payload(row),
        )

    def _review_row(self, transaction: TransactionContext, ctx: RequestContext, review_id: str) -> InsightReviewRow:
        row = self.insight_review_repository.review_by_id(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            review_id=review_id,
        )
        if row is None:
            raise NotFound("insight review not found", details={"review_id": review_id})
        return row

    def _claim_execution(self, transaction: TransactionContext, ctx: RequestContext, review_id: str) -> None:
        row = self.insight_review_repository.mark_execution_started(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            review_id=review_id,
            updated_at=_now(),
        )
        if row is None:
            raise ConflictDetected("approval execution was already claimed", details={"review_id": review_id})

    def _ledger(self, transaction: TransactionContext, ctx: RequestContext, ai_run_id: str) -> Mapping[str, object]:
        ledger = self.ai_run_repository.ledger_for_run(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            ai_run_id=ai_run_id,
        )
        if ledger is None:
            raise ApprovalExecutionError("run_not_found", f"AI run {ai_run_id} was not found")
        return ledger

    def _action_type(
        self, transaction: TransactionContext, ctx: RequestContext, prepared: _PreparedExecution
    ) -> Mapping[str, object]:
        try:
            action = self.ontology_service._active_action_type(transaction, ctx, prepared.action_type)
        except NotFound as exc:
            raise ApprovalExecutionError("action_not_found", f"action {prepared.action_type} was not found") from exc
        if action["target_api_name"] != prepared.target_object_type:
            raise ApprovalExecutionError("action_target_mismatch", "approved proposal target no longer matches action")
        return action

    def _target_record(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        action: Mapping[str, object],
        prepared: _PreparedExecution,
    ) -> Mapping[str, object]:
        record = self.object_records_service._object_record(
            transaction,
            ctx,
            prepared.target_object_type,
            prepared.target_object_id,
            _required_text(action, "target_object_type_id"),
        )
        if record is None:
            raise ApprovalExecutionError("target_not_found", "approved proposal target object was not found")
        if record["object_version"] != prepared.expected_object_version:
            raise ApprovalExecutionError(
                "approval_object_version_conflict",
                "approved proposal expected object version is stale",
            )
        return record

    def _require_reviewer(self, ctx: RequestContext, review_id: str) -> None:
        try:
            self.policy.require(ctx, "insight:review")
        except PermissionDenied as exc:
            raise ApprovalExecutionError("policy_denied", f"permission denied for review {review_id}") from exc

    def _require_action_permission(self, ctx: RequestContext, action_type: str) -> None:
        permission = f"action:execute:{action_type}"
        try:
            self.policy.require(ctx, permission)
        except PermissionDenied as exc:
            raise ApprovalExecutionError("policy_denied", f"permission denied for {permission}") from exc

    def _require_evidence_access(self, ctx: RequestContext, evidence_refs: Sequence[JsonObject]) -> None:
        for ref in evidence_refs:
            permission = source_permission_for_type(_required_text(ref, "sourceResourceType"))
            try:
                self.policy.require(ctx, permission)
            except PermissionDenied as exc:
                raise ApprovalExecutionError("source_access_denied", f"permission denied for {permission}") from exc

    def _require_write_open(self, ctx: RequestContext, review_id: str) -> None:
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="execute_approved_action",
            resource_type="insight_review",
            resource_id=review_id,
        )

    def _record_execution_evidence(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        prepared: _PreparedExecution,
        action_run_id: str,
        response: Mapping[str, object],
    ) -> None:
        self.runtime_service._run_relation(
            transaction,
            ctx,
            source_run_type="insight_review",
            source_run_id=prepared.review_id,
            target_run_type="action",
            target_run_id=action_run_id,
            relation="approved_as",
            resource_type="action_run",
            resource_id=action_run_id,
            metadata={"proposalFingerprint": prepared.proposal_fingerprint},
        )
        self.runtime_service._audit(
            transaction,
            ctx,
            event_type="aip.approval_execution.executed",
            resource_type="insight_review",
            resource_id=prepared.review_id,
            action="aip:execute_approved_action",
            after_ref={"actionRunId": action_run_id, "actionResponse": dict(response)},
            correlation_id=action_run_id,
        )

    def _mark_failed(self, ctx: RequestContext, review_id: str, exc: Exception) -> None:
        with self.engine.begin() as transaction:
            self.insight_review_repository.mark_execution_failed(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                review_id=review_id,
                error=self.runtime_service._error_payload(exc, ctx, run_id=review_id, correlation_id=ctx.request_id),
                updated_at=_now(),
            )


def _validate_request(request: ApprovalExecutionRequest) -> None:
    if not request.review_id:
        raise ApprovalExecutionError("missing_field", "review_id is required")
    if not request.expected_proposal_fingerprint.startswith("sha256:"):
        raise ApprovalExecutionError("missing_field", "expected proposal fingerprint must be sha256-prefixed")


def _approved_pending_proposal(row: InsightReviewRow) -> Mapping[str, object]:
    if row["status"] != "approved":
        raise ApprovalExecutionError("review_not_approved", "insight review must be approved before execution")
    if row["execution_status"] not in {None, "pending_review"}:
        raise ConflictDetected(
            "approval execution is already in progress or terminal",
            details={"review_id": row["id"]},
        )
    if row["proposal_type"] != _PROPOSAL_TYPE or row["action_proposal"] is None:
        raise ApprovalExecutionError("not_action_proposal", "insight review does not contain an action proposal")
    return row["action_proposal"]


def _require_matching_fingerprint(row: InsightReviewRow, expected: str) -> None:
    stored = row["proposal_fingerprint"]
    proposal = row["action_proposal"] or {}
    proposal_fingerprint = proposal.get("proposalFingerprint")
    if stored != expected or proposal_fingerprint != expected:
        raise ApprovalExecutionError("fingerprint_mismatch", "approved proposal fingerprint does not match")


def _prepared_execution(row: InsightReviewRow, proposal: Mapping[str, object]) -> _PreparedExecution:
    policy_version = _required_text(proposal, "policyVersion")
    if row["approval_policy_version"] != policy_version:
        raise ApprovalExecutionError("fingerprint_mismatch", "approval policy version does not match proposal")
    return _PreparedExecution(
        review_id=row["id"],
        proposal_fingerprint=_required_row_text(row, "proposal_fingerprint"),
        originating_ai_run_id=_required_row_text(row, "originating_ai_run_id"),
        action_type=_required_text(proposal, "actionType"),
        target_object_type=_required_text(proposal, "targetObjectType"),
        target_object_id=_required_text(proposal, "targetObjectId"),
        expected_object_version=_required_int(proposal, "expectedObjectVersion"),
        parameters=_required_mapping(proposal, "parameters"),
        policy_version=policy_version,
    )


def _require_recomputed_fingerprint(
    prepared: _PreparedExecution,
    evidence_refs: Sequence[JsonObject],
    run: Mapping[str, object],
) -> None:
    fingerprint = compute_action_proposal_fingerprint(
        action_type=prepared.action_type,
        target_object_type=prepared.target_object_type,
        target_object_id=prepared.target_object_id,
        expected_object_version=prepared.expected_object_version,
        parameters=prepared.parameters,
        evidence_refs=evidence_refs,
        agent_version_id=_required_text(run, "agent_version_id"),
        policy_version=prepared.policy_version,
    )
    if fingerprint != prepared.proposal_fingerprint:
        raise ApprovalExecutionError("fingerprint_mismatch", "approved proposal was changed after review")


def _require_not_expired(proposal: Mapping[str, object]) -> None:
    expires_at = _required_text(proposal, "expiresAt")
    if _parse_instant(expires_at) <= _parse_instant(_now()):
        raise ApprovalExecutionError("review_expired", "approved proposal has expired")


def _evidence_refs(proposal: Mapping[str, object]) -> list[JsonObject]:
    refs = proposal.get("evidenceRefs")
    if not isinstance(refs, list) or not refs:
        raise ApprovalExecutionError("missing_evidence", "approved proposal requires evidence references")
    if not all(isinstance(ref, dict) for ref in refs):
        raise ApprovalExecutionError("invalid_proposal", "proposal evidence references must be objects")
    return [cast(JsonObject, ref) for ref in refs]


def _required_row_text(row: InsightReviewRow, key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ApprovalExecutionError("invalid_review", f"review is missing {key}")
    return value


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ApprovalExecutionError("invalid_proposal", f"proposal is missing {key}")
    return value


def _required_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int):
        raise ApprovalExecutionError("invalid_proposal", f"proposal is missing integer {key}")
    return value


def _required_mapping(payload: Mapping[str, object], key: str) -> JsonObject:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ApprovalExecutionError("invalid_proposal", f"proposal is missing {key}")
    return value


def _parse_instant(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApprovalExecutionError("invalid_expiration", "proposal expiration is not a valid ISO timestamp") from exc
