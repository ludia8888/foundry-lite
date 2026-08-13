"""Worker context, lease, and frozen-plan helpers for Action execution."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from foundry_lite.application.services.action_attempt_heartbeat import ActionAttemptHeartbeat
from foundry_lite.application.services.action_criteria_resolution import (
    with_criteria_expectations as with_criteria_expectations,
)
from foundry_lite.application.services.action_distributed_contracts import (
    ActionAsyncRunRow,
    ActionDefinitionV3,
    ActionExecutionRepository,
    ActionFunctionExecutionRequest,
    ActionFunctionExecutionResult,
    ActionObjectIndexer,
    ActionObjectRecordLookup,
    ActionRepository,
    ActionRunRetryableFailure,
    ActionRuntimeBoundary,
    ActionStepAttemptClaim,
    ActionStepAttemptRow,
    ConflictDetected,
    EditPlan,
    InvariantViolation,
    OntologyLookupService,
    RequestContext,
    StatusTransition,
    TransactionContext,
    TransactionManager,
    action_contract_fingerprint,
    compile_action_contract_snapshot,
)
from foundry_lite.application.services.action_distributed_run_evidence import (
    action_function_output,
    append_action_attempt_event,
)
from foundry_lite.application.services.action_edit_plan_committer import ActionEditPlanCommitter
from foundry_lite.application.services.action_function_batch import stored_action_function_batch_items
from foundry_lite.application.state_transitions import ACTION_RUN_COMMIT_PENDING
from foundry_lite.domain.action_runtime.action_execution_plan import (
    criteria_read_expectations_from_manifest as criteria_read_expectations_from_manifest,
)
from foundry_lite.domain.action_runtime.action_execution_plan import (
    edit_plan_from_manifest,
    seal_action_execution_plan,
)
from foundry_lite.domain.action_runtime.ontology_edit_batch import OntologyEditBatch


@dataclass(frozen=True, slots=True)
class ActionWorkerLease:
    worker_id: str
    lease_token: str
    heartbeat_at: str
    expires_at: str


class ActionStepLeaseLost(ActionRunRetryableFailure):
    """Stop a stale worker before it can write a result."""


def action_attempt_heartbeat(
    engine: TransactionManager,
    repository: ActionExecutionRepository,
    attempt: ActionStepAttemptRow,
) -> ActionAttemptHeartbeat:
    """Create the fenced heartbeat controller for a claimed step attempt."""
    return ActionAttemptHeartbeat(engine, repository, attempt)


def action_worker_context(row: ActionAsyncRunRow) -> RequestContext:
    snapshot = _mapping(row["execution_plan"], "executionPlan")
    principal = _mapping(snapshot.get("principal"), "principal")
    return RequestContext(
        tenant_id=row["tenant_id"],
        actor_user_id=_text(principal, "actorUserId"),
        request_id=f"action-run:{row['id']}",
        roles=_strings(principal.get("roles")),
        application_id=_optional_text(principal.get("applicationId")),
        client_id=_optional_text(principal.get("clientId")),
        token_scopes=_strings(principal.get("tokenScopes")),
        oauth_session_id=_optional_text(principal.get("oauthSessionId")),
        user_attributes=_mapping(principal.get("userAttributes"), "userAttributes"),
    )


def action_worker_lease(worker_id: str) -> ActionWorkerLease:
    now = datetime.now(UTC)
    seconds = max(1, int(os.getenv("FOUNDRY_LITE_ACTION_STEP_LEASE_SECONDS", "300")))
    return ActionWorkerLease(worker_id, uuid4().hex, _timestamp(now), _timestamp(now + timedelta(seconds=seconds)))


def action_attempt_claim(
    row: ActionAsyncRunRow, step_key: str, lease: ActionWorkerLease, *, is_cancellation: bool = False
) -> ActionStepAttemptClaim:
    return ActionStepAttemptClaim(
        tenant_id=row["tenant_id"],
        run_id=row["id"],
        step_key=step_key,
        worker_id=lease.worker_id,
        lease_token=lease.lease_token,
        lease_expires_at=lease.expires_at,
        claimed_at=lease.heartbeat_at,
        input_manifest={"planHash": row["plan_hash"] or ""},
        is_cancellation=is_cancellation,
    )


def stored_action_contract(row: ActionAsyncRunRow) -> ActionDefinitionV3:
    snapshot = _mapping(row["execution_plan"], "executionPlan")
    return compile_action_contract_snapshot(_mapping(snapshot.get("contract"), "contract"))


def action_function_request(
    row: ActionAsyncRunRow,
    ctx: RequestContext,
    effect_outputs: Mapping[str, object] | None = None,
) -> ActionFunctionExecutionRequest:
    snapshot = _mapping(row["execution_plan"], "executionPlan")
    contract = stored_action_contract(row)
    if contract.function is None:
        raise InvariantViolation("Action run has no pinned function")
    return ActionFunctionExecutionRequest(
        tenant_id=ctx.tenant_id,
        run_id=row["id"],
        request_id=ctx.request_id,
        actor_user_id=ctx.actor_user_id,
        roles=ctx.roles,
        token_scopes=ctx.token_scopes,
        application_id=ctx.application_id,
        client_id=ctx.client_id,
        ontology_version_id=_text(snapshot, "ontologyVersionId"),
        function_api_name=contract.function.api_name,
        function_version=contract.function.version,
        inputs=dict(row["parameters"]),
        effect_outputs=dict(effect_outputs or {}),
        user_attributes=ctx.user_attributes,
    )


def action_function_requests(
    row: ActionAsyncRunRow,
    ctx: RequestContext,
    effect_outputs: Mapping[str, object] | None = None,
) -> tuple[ActionFunctionExecutionRequest, ...]:
    """Build one request per invocation, or one list-of-struct request for batch mode."""
    snapshot = _mapping(row["execution_plan"], "executionPlan")
    items = stored_action_function_batch_items(snapshot)
    contract = stored_action_contract(row)
    function = contract.function
    if function is None:
        raise InvariantViolation("stored Action function run has no function")
    if not items:
        if function.execution_mode == "batched":
            if function.batch_input_name is None:
                raise InvariantViolation("batched Action function has no batch input name")
            single_inputs = {function.batch_input_name: [dict(row["parameters"])]}
            return (_action_function_request(row, ctx, single_inputs, effect_outputs, index=0),)
        return (action_function_request(row, ctx, effect_outputs),)
    parameters = tuple(dict(_mapping(item.get("parameters"), "parameters")) for item in items)
    if function.execution_mode == "batched":
        if function.batch_input_name is None:
            raise InvariantViolation("batched Action function has no batch input name")
        invocation_inputs: Sequence[Mapping[str, object]] = ({function.batch_input_name: list(parameters)},)
    else:
        invocation_inputs = parameters
    return tuple(
        _action_function_request(row, ctx, values, effect_outputs, index=index)
        for index, values in enumerate(invocation_inputs)
    )


def combine_action_function_results(
    results: Sequence[ActionFunctionExecutionResult],
) -> ActionFunctionExecutionResult:
    if not results:
        raise InvariantViolation("Action function execution produced no result")
    if len(results) == 1:
        return results[0]
    edit_batch = OntologyEditBatch.combine(tuple(result.edit_batch for result in results))
    hashes = [result.result_hash for result in results]
    encoded = json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    result_hash = f"sha256:{hashlib.sha256(encoded).hexdigest()}"
    return ActionFunctionExecutionResult(
        edit_batch=edit_batch,
        external_execution_id=f"action-function-batch:{result_hash.removeprefix('sha256:')[:24]}",
        result_hash=result_hash,
        provenance={"executionMode": "per_request", "invocations": [dict(item.provenance) for item in results]},
    )


def _action_function_request(
    row: ActionAsyncRunRow,
    ctx: RequestContext,
    inputs: Mapping[str, object],
    effect_outputs: Mapping[str, object] | None,
    *,
    index: int,
) -> ActionFunctionExecutionRequest:
    request = action_function_request(row, ctx, effect_outputs)
    return ActionFunctionExecutionRequest(
        tenant_id=request.tenant_id,
        run_id=f"{request.run_id}:invocation:{index}",
        request_id=request.request_id,
        actor_user_id=request.actor_user_id,
        roles=request.roles,
        token_scopes=request.token_scopes,
        application_id=request.application_id,
        client_id=request.client_id,
        ontology_version_id=request.ontology_version_id,
        function_api_name=request.function_api_name,
        function_version=request.function_version,
        inputs=dict(inputs),
        effect_outputs=request.effect_outputs,
        user_attributes=request.user_attributes,
    )


def plan_manifest(row: ActionAsyncRunRow) -> Mapping[str, object]:
    snapshot = _mapping(row["execution_plan"], "executionPlan")
    return _mapping(snapshot.get("editManifest"), "editManifest")


def stored_edit_plan(row: ActionAsyncRunRow) -> EditPlan:
    return edit_plan_from_manifest(plan_manifest(row))


def require_stored_plan_hash(row: ActionAsyncRunRow) -> None:
    snapshot = dict(_mapping(row["execution_plan"], "executionPlan"))
    snapshot.pop("contract", None)
    snapshot.pop("principal", None)
    snapshot.pop("externalMcpApproval", None)
    sealed = seal_action_execution_plan(snapshot)
    if sealed["planHash"] != row["plan_hash"]:
        raise InvariantViolation("stored Action execution plan hash does not match")


def required_action_run(
    repository: ActionExecutionRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    run_id: str,
) -> ActionAsyncRunRow:
    row = repository.run_by_id(transaction=transaction, tenant_id=ctx.tenant_id, run_id=run_id)
    if row is None:
        raise InvariantViolation("Action run disappeared")
    return row


def load_action_run(
    engine: TransactionManager,
    repository: ActionExecutionRepository,
    tenant_id: str,
    run_id: str,
) -> ActionAsyncRunRow:
    with engine.begin() as transaction:
        row = repository.run_by_id(transaction=transaction, tenant_id=tenant_id, run_id=run_id)
    if row is None:
        raise InvariantViolation("Action run not found")
    return row


def action_plan_committer(
    action_repository: ActionRepository,
    object_indexer: ActionObjectIndexer,
    object_lookup: ActionObjectRecordLookup,
    ontology_lookup: OntologyLookupService,
    runtime: ActionRuntimeBoundary,
) -> ActionEditPlanCommitter:
    return ActionEditPlanCommitter(
        action_repository=action_repository,
        object_indexer=object_indexer,
        object_lookup=object_lookup,
        ontology_lookup=ontology_lookup,
        link_type_lookup=ontology_lookup,
        runtime=runtime,
    )


def append_worker_attempt_event(
    repository: ActionExecutionRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    row: ActionAsyncRunRow,
    attempt: ActionStepAttemptRow,
    event_type: str,
    payload: dict[str, object],
) -> None:
    append_action_attempt_event(repository, transaction, ctx, row, attempt, event_type, payload)


def action_success_output(
    contract: ActionDefinitionV3,
    function_result: ActionFunctionExecutionResult | None,
    before_effect: dict[str, object] | None,
    committed_plan: dict[str, object],
    effect_receipt_ids: list[str],
) -> dict[str, object]:
    return {
        "plan": committed_plan,
        "function": action_function_output(function_result),
        "beforeEffect": before_effect,
        "afterEffectReceiptIds": effect_receipt_ids,
        "externalExecutionId": function_result.external_execution_id if function_result else None,
        "definitionFingerprint": action_contract_fingerprint(contract),
    }


def persist_terminal_action_failure(
    repository: ActionExecutionRepository,
    runtime: ActionRuntimeBoundary,
    transaction: TransactionContext,
    ctx: RequestContext,
    row: ActionAsyncRunRow,
    attempt: ActionStepAttemptRow,
    transition: StatusTransition,
    error: dict[str, object],
    changed_at: str,
) -> None:
    repository.transition_run(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        run_id=row["id"],
        transition=transition,
        changed_at=changed_at,
        error=error,
    )
    event_type = f"action.run.{transition.to_status}"
    append_worker_attempt_event(repository, transaction, ctx, row, attempt, event_type, error)
    runtime._audit(
        transaction,
        ctx,
        event_type=event_type,
        resource_type="action_run",
        resource_id=row["id"],
        action="execute",
        decision="deny",
        after_ref=error,
        correlation_id=row["id"],
    )


def require_action_attempt_owner(
    repository: ActionExecutionRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    attempt: ActionStepAttemptRow,
    owned_at: str,
) -> ActionStepAttemptRow:
    owner = repository.lock_attempt_owner(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        attempt_id=attempt["id"],
        worker_id=attempt["worker_id"],
        lease_token=attempt["lease_token"],
        fencing_token=attempt["fencing_token"],
        owned_at=owned_at,
    )
    if owner is None:
        raise ActionStepLeaseLost("Action step fencing token is stale")
    return owner


def complete_action_attempt(
    repository: ActionExecutionRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    attempt: ActionStepAttemptRow,
    status: str,
    output: dict[str, object],
    error: dict[str, object] | None,
    error_kind: str | None,
    changed_at: str,
    retry_at: str | None = None,
) -> ActionStepAttemptRow:
    completed = repository.complete_attempt(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        attempt_id=attempt["id"],
        worker_id=attempt["worker_id"],
        lease_token=attempt["lease_token"],
        fencing_token=attempt["fencing_token"],
        status=status,
        output_manifest=output,
        error=error,
        error_kind=error_kind,
        completed_at=changed_at,
        retry_at=retry_at,
        external_execution_id=str(output.get("externalExecutionId") or "") or None,
    )
    if completed is None:
        raise ActionStepLeaseLost("Action step terminal write was fenced")
    return completed


def mark_action_commit_pending(
    repository: ActionExecutionRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    row: ActionAsyncRunRow,
    changed_at: str,
) -> None:
    updated = repository.transition_run(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        run_id=row["id"],
        transition=ACTION_RUN_COMMIT_PENDING,
        changed_at=changed_at,
    )
    if updated is None:
        raise ConflictDetected("Action run changed before commit")


def utc_now() -> str:
    return _timestamp(datetime.now(UTC))


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _mapping(raw: object, field: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise InvariantViolation("stored Action execution field is invalid", details={"field": field})
    return raw


def _text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise InvariantViolation("stored Action execution text is invalid", details={"field": key})
    return value


def _optional_text(raw: object) -> str | None:
    return raw if isinstance(raw, str) and raw else None


def _strings(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list | tuple) or not all(isinstance(item, str) for item in raw):
        raise InvariantViolation("stored Action principal sequence is invalid")
    return tuple(raw)
