"""Worker context, lease, and frozen-plan helpers for Action execution."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from foundry_lite.application.action_async_execution_types import ActionAsyncRunRow, ActionStepAttemptClaim
from foundry_lite.application.ports.action_function_executor import ActionFunctionExecutionRequest
from foundry_lite.domain.action_runtime.action_contract import ActionDefinitionV3, compile_action_contract
from foundry_lite.domain.action_runtime.action_execution_plan import edit_plan_from_manifest, seal_action_execution_plan
from foundry_lite.domain.action_runtime.edit_plan import EditPlan
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation


@dataclass(frozen=True, slots=True)
class ActionWorkerLease:
    worker_id: str
    lease_token: str
    heartbeat_at: str
    expires_at: str


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
    return compile_action_contract(_mapping(snapshot.get("contract"), "contract"))


def action_function_request(row: ActionAsyncRunRow, ctx: RequestContext) -> ActionFunctionExecutionRequest:
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
    sealed = seal_action_execution_plan(snapshot)
    if sealed["planHash"] != row["plan_hash"]:
        raise InvariantViolation("stored Action execution plan hash does not match")


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
