"""Pure request, record, and event helpers for Action async runs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from foundry_lite.application.action_async_execution_types import (
    ActionAsyncRunRecord,
    ActionRunEventRecord,
    ActionRunStepRecord,
)
from foundry_lite.application.ports import ActionTypeRow
from foundry_lite.application.ports.action_run_orchestrator import ActionRunDispatchRequest
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.domain.action_runtime.action_contract import action_contract_payload, compile_action_contract
from foundry_lite.domain.context import RequestContext


def async_request_fingerprint(
    ctx: RequestContext,
    action_api_name: str,
    object_type: str,
    object_id: str,
    expected_object_version: int,
    params: Mapping[str, object],
) -> str:
    payload = {
        "actionApiName": action_api_name,
        "applicationId": ctx.application_id,
        "clientId": ctx.client_id,
        "expectedObjectVersion": expected_object_version,
        "objectId": object_id,
        "objectType": object_type,
        "parameters": dict(params),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def action_execution_snapshot(
    action_type: ActionTypeRow,
    plan: Mapping[str, object],
    ctx: RequestContext,
) -> dict[str, object]:
    return {
        **dict(plan),
        "contract": action_contract_payload(compile_action_contract(action_type["definition"])),
        "principal": {
            "actorUserId": ctx.actor_user_id,
            "roles": list(ctx.roles),
            "applicationId": ctx.application_id,
            "clientId": ctx.client_id,
            "tokenScopes": list(ctx.token_scopes),
        },
    }


def async_run_record(
    ctx: RequestContext,
    action_type: ActionTypeRow,
    plan: Mapping[str, object],
    snapshot: dict[str, object],
    idempotency_key: str,
    request_fingerprint: str,
    target_object_type_id: str,
) -> ActionAsyncRunRecord:
    target = _mapping(plan["target"])
    return ActionAsyncRunRecord(
        run_id=_new_id("action_run"),
        tenant_id=ctx.tenant_id,
        action_type_id=action_type["id"],
        action_api_name=action_type["api_name"],
        actor_user_id=ctx.actor_user_id,
        target_object_type_id=target_object_type_id,
        target_object_type=str(target["objectType"]),
        target_object_id=str(target["objectId"]),
        expected_object_version=_version(target.get("expectedObjectVersion")),
        parameters=dict(_mapping(plan["parameters"])),
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        definition_version=str(plan["definitionFingerprint"]),
        plan_hash=str(plan["planHash"]),
        execution_plan=snapshot,
        created_at=_now(),
    )


def async_run_steps(record: ActionAsyncRunRecord, plan: Mapping[str, object]) -> tuple[ActionRunStepRecord, ...]:
    kind = "function" if plan.get("functionVersion") else "commit"
    return (
        ActionRunStepRecord(
            step_id=f"{record.run_id}:step:{kind}",
            tenant_id=record.tenant_id,
            run_id=record.run_id,
            step_key=kind,
            step_kind=kind,
            input_manifest={"planHash": record.plan_hash},
            created_at=record.created_at,
        ),
    )


def action_run_event(
    ctx: RequestContext,
    run_id: str,
    event_type: str,
    payload: dict[str, object],
) -> ActionRunEventRecord:
    return ActionRunEventRecord(
        event_id=_new_id("aevent"),
        tenant_id=ctx.tenant_id,
        run_id=run_id,
        event_type=event_type,
        payload=payload,
        created_at=_now(),
    )


def action_dispatch_request(ctx: RequestContext, row: Mapping[str, object]) -> ActionRunDispatchRequest:
    return ActionRunDispatchRequest(
        tenant_id=ctx.tenant_id,
        run_id=str(row["id"]),
        action_api_name=str(row["action_type_api_name"]),
        request_id=ctx.request_id,
        idempotency_key=str(row["idempotency_key"]),
        execution_plan=_mapping(row["execution_plan"]),
    )


def _mapping(raw: object) -> dict[str, object]:
    if not isinstance(raw, Mapping):
        raise ValueError("Action execution plan contains an invalid object")
    return {str(key): value for key, value in raw.items()}


def _version(raw: object) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        raise ValueError("Action execution target version is invalid")
    return raw
