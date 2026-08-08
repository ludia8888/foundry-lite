"""Narrow durable Action-run orchestration for Consumer MCP service principals."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.action_async_execution_types import ActionAsyncRunRow
from foundry_lite.application.services.action_async_run_support import async_request_fingerprint
from foundry_lite.application.services.action_protocols import ActionOsdkScopeBoundary, ActionPlanningBoundary
from foundry_lite.application.services.osdk_service_principal_authorization import (
    ServicePrincipalAccessSessionBoundary,
    require_service_principal_scope,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed

_TERMINAL = frozenset(
    {"succeeded", "failed", "cancelled", "conflict", "outcome_unknown", "compensation_required", "reconciled"}
)


class ExternalMcpActionRunHost(Protocol):
    action_planning_service: ActionPlanningBoundary

    def _external_mcp_authorizers(
        self,
    ) -> tuple[ServicePrincipalAccessSessionBoundary, ActionOsdkScopeBoundary]: ...

    def _existing_run_unchecked(
        self, ctx: RequestContext, action_api_name: str, idempotency_key: str
    ) -> ActionAsyncRunRow | None: ...

    def _create_run(
        self,
        ctx: RequestContext,
        action_api_name: str,
        plan: Mapping[str, object],
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[ActionAsyncRunRow, bool]: ...

    def _dispatch(self, ctx: RequestContext, row: ActionAsyncRunRow) -> None: ...

    def _external_run_snapshot(
        self, ctx: RequestContext, run_id: str
    ) -> tuple[ActionAsyncRunRow, dict[str, object]]: ...

    def _require_external_write_open(self, ctx: RequestContext, action_api_name: str) -> None: ...


def start_external_mcp_action_run(
    host: ExternalMcpActionRunHost,
    action_api_name: str,
    *,
    object_type: str,
    object_id: str,
    expected_object_version: int,
    params: Mapping[str, object],
    idempotency_key: str,
    wait_seconds: int,
    ctx: RequestContext,
) -> dict[str, object]:
    _require_start_values(idempotency_key, wait_seconds)
    _require_external_scope(host, ctx, action_api_name)
    host._require_external_write_open(ctx, action_api_name)
    fingerprint = async_request_fingerprint(
        ctx, action_api_name, object_type, object_id, expected_object_version, params
    )
    existing = host._existing_run_unchecked(ctx, action_api_name, idempotency_key)
    if existing is not None:
        return _resume_external_row(host, ctx, existing, fingerprint, wait_seconds)
    plan = host.action_planning_service.plan_external_mcp_action(
        action_api_name,
        object_type=object_type,
        object_id=object_id,
        expected_object_version=expected_object_version,
        params=params,
        ctx=ctx,
    )
    _require_external_autonomous_plan(plan)
    row, is_created = host._create_run(ctx, action_api_name, plan, idempotency_key, fingerprint)
    if not is_created:
        _require_replay(row, fingerprint)
    host._dispatch(ctx, row)
    return _wait_for_external_snapshot(host, ctx, str(row["id"]), wait_seconds)


def resume_external_mcp_action_run(
    host: ExternalMcpActionRunHost,
    action_api_name: str,
    *,
    object_type: str,
    object_id: str,
    expected_object_version: int,
    params: Mapping[str, object],
    idempotency_key: str,
    ctx: RequestContext,
) -> dict[str, object] | None:
    _require_external_scope(host, ctx, action_api_name)
    fingerprint = async_request_fingerprint(
        ctx, action_api_name, object_type, object_id, expected_object_version, params
    )
    existing = host._existing_run_unchecked(ctx, action_api_name, idempotency_key)
    if existing is None:
        return None
    return _resume_external_row(host, ctx, existing, fingerprint, 0)


def get_external_mcp_action_run(
    host: ExternalMcpActionRunHost, run_id: str, *, ctx: RequestContext
) -> dict[str, object]:
    row, snapshot = host._external_run_snapshot(ctx, run_id)
    _require_external_run_owner(host, ctx, row)
    return snapshot


def _require_external_scope(host: ExternalMcpActionRunHost, ctx: RequestContext, action_api_name: str) -> None:
    access_sessions, application_scopes = host._external_mcp_authorizers()
    require_service_principal_scope(
        ctx,
        access_sessions,
        application_scopes,
        resource_type="action",
        resource_api_name=action_api_name,
        operation="execute",
    )


def _require_external_run_owner(host: ExternalMcpActionRunHost, ctx: RequestContext, row: ActionAsyncRunRow) -> None:
    _require_external_scope(host, ctx, str(row["action_type_api_name"]))
    access_sessions, application_scopes = host._external_mcp_authorizers()
    require_service_principal_scope(
        ctx,
        access_sessions,
        application_scopes,
        resource_type="object",
        resource_api_name=str(row["target_object_type_api_name"]),
        operation="read",
    )
    snapshot = row.get("execution_plan")
    principal = snapshot.get("principal") if isinstance(snapshot, Mapping) else None
    if (
        not isinstance(principal, Mapping)
        or principal.get("applicationId") != ctx.application_id
        or principal.get("clientId") != ctx.client_id
        or not _is_external_run_owner(row, snapshot, ctx)
    ):
        raise NotFound("Action run not found", details={"actionRunId": row["id"]})


def _is_external_run_owner(row: ActionAsyncRunRow, snapshot: object, ctx: RequestContext) -> bool:
    if row["actor_user_id"] == ctx.actor_user_id:
        return True
    if not isinstance(snapshot, Mapping):
        return False
    approval = snapshot.get("externalMcpApproval")
    return bool(
        isinstance(approval, Mapping)
        and approval.get("source") == "ontology_mcp"
        and approval.get("servicePrincipalId") == ctx.actor_user_id
        and approval.get("applicationId") == ctx.application_id
        and approval.get("clientId") == ctx.client_id
        and isinstance(approval.get("reviewId"), str)
        and approval.get("reviewId")
    )


def _resume_external_row(
    host: ExternalMcpActionRunHost,
    ctx: RequestContext,
    row: ActionAsyncRunRow,
    fingerprint: str,
    wait_seconds: int,
) -> dict[str, object]:
    _require_external_run_owner(host, ctx, row)
    _require_replay(row, fingerprint)
    host._dispatch(ctx, row)
    return _wait_for_external_snapshot(host, ctx, str(row["id"]), wait_seconds)


def _wait_for_external_snapshot(
    host: ExternalMcpActionRunHost, ctx: RequestContext, run_id: str, wait_seconds: int
) -> dict[str, object]:
    deadline = time.monotonic() + wait_seconds
    while True:
        snapshot = get_external_mcp_action_run(host, run_id, ctx=ctx)
        if snapshot["status"] in _TERMINAL or time.monotonic() >= deadline:
            return snapshot
        time.sleep(0.1)


def _require_start_values(idempotency_key: str, wait_seconds: int) -> None:
    if not idempotency_key.strip():
        raise ValidationFailed("Idempotency-Key is required")
    if wait_seconds < 0 or wait_seconds > 30:
        raise ValidationFailed("waitSeconds must be between 0 and 30")


def _require_replay(row: ActionAsyncRunRow, request_fingerprint: str) -> None:
    if row["request_fingerprint"] != request_fingerprint:
        raise ConflictDetected("Idempotency-Key was already used with a different Action request")


def _require_external_autonomous_plan(plan: Mapping[str, object]) -> None:
    approval = plan.get("approval")
    if not isinstance(approval, Mapping) or approval.get("canAgentExecuteAutonomously") is not True:
        raise ValidationFailed("external MCP service principal Action requires human approval")
    if plan.get("functionVersion") is not None or plan.get("effectManifest"):
        raise ValidationFailed("external MCP autonomous Action must be a local deterministic edit")
