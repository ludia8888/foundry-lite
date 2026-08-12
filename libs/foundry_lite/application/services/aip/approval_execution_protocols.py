"""Narrow collaborator boundaries used by approved Action execution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.action_types import ActionExecutionPlanResponse
from foundry_lite.application.ports import ObjectTypeRow, OsdkResourceOperation, OsdkResourceType, TransactionContext
from foundry_lite.domain.context import RequestContext


class ActionRunner(Protocol):
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

    def start_action_run(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        wait_seconds: int,
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object]: ...


class ActionPlanner(Protocol):
    def plan_action(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        ctx: RequestContext | None = None,
        is_dry_run: bool = False,
    ) -> ActionExecutionPlanResponse: ...


class OntologyLookup(Protocol):
    def _active_action_type(
        self, transaction: TransactionContext, ctx: RequestContext, action_api_name: str
    ) -> Mapping[str, object]: ...

    def _active_object_type(
        self, transaction: TransactionContext, ctx: RequestContext, object_type_api_name: str
    ) -> ObjectTypeRow: ...


class ObjectRecordLookup(Protocol):
    def _object_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        object_id: str,
        object_type_id: str | None = None,
    ) -> Mapping[str, object] | None: ...


class RuntimeBoundary(Protocol):
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


class OsdkApplicationBoundary(Protocol):
    def require_mcp_enabled(self, ctx: RequestContext, app_id: str, *, origin: str | None = None) -> None: ...

    def require_resource_scope(
        self,
        ctx: RequestContext,
        *,
        resource_type: OsdkResourceType,
        resource_api_name: str,
        operation: OsdkResourceOperation,
    ) -> None: ...
