"""Narrow runtime contracts for the Consumer Ontology MCP gateway."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.action_types import ActionCatalogItem, ActionExecutionPlanResponse
from foundry_lite.application.ports import (
    FunctionTypeRow,
    ObjectPayload,
    ObjectQueryResult,
    OsdkMcpSessionEventRow,
    OsdkMcpStreamLease,
    OsdkResourceOperation,
    OsdkResourceType,
)
from foundry_lite.application.services.function_execution_service import FunctionExecutionResult
from foundry_lite.application.services.mcp_json_rpc import JsonRpcRequestId
from foundry_lite.application.services.mcp_session_namespace import require_mcp_session_namespace
from foundry_lite.domain.context import RequestContext

JsonObject = Mapping[str, object]


def require_ontology_mcp_session_namespace(session_id: str) -> None:
    require_mcp_session_namespace(session_id, "ontology")


@dataclass(frozen=True, slots=True)
class OntologyMcpToolCall:
    application_id: str
    session_id: str
    json_rpc_id: JsonRpcRequestId
    tool_name: str
    arguments: JsonObject
    origin: str | None = None


class OntologyMcpApplicationRuntime(Protocol):
    def runtime_resource_grants(self, ctx: RequestContext, *, application_id: str) -> list[Mapping[str, object]]: ...

    def require_resource_scope(
        self,
        ctx: RequestContext,
        *,
        resource_type: OsdkResourceType,
        resource_api_name: str,
        operation: OsdkResourceOperation,
    ) -> None: ...

    def require_mcp_enabled(self, ctx: RequestContext, app_id: str, *, origin: str | None = None) -> None: ...

    def open_mcp_session(
        self, ctx: RequestContext, app_id: str, session_id: str, *, origin: str | None = None
    ) -> Mapping[str, object]: ...

    def resume_mcp_session(
        self, ctx: RequestContext, app_id: str, session_id: str, *, origin: str | None = None
    ) -> Mapping[str, object]: ...

    def record_mcp_session_event(
        self,
        ctx: RequestContext,
        app_id: str,
        session_id: str,
        *,
        event_type: str,
        payload: dict[str, object],
    ) -> Mapping[str, object]: ...

    def list_mcp_session_events(
        self, ctx: RequestContext, app_id: str, session_id: str, *, after_sequence: int = 0
    ) -> list[OsdkMcpSessionEventRow]: ...

    def claim_mcp_session_stream(
        self, ctx: RequestContext, app_id: str, session_id: str, *, origin: str | None = None
    ) -> OsdkMcpStreamLease: ...

    def release_mcp_session_stream(self, ctx: RequestContext, app_id: str, session_id: str, lease_id: str) -> bool: ...

    def close_mcp_session(self, ctx: RequestContext, app_id: str, session_id: str) -> Mapping[str, object]: ...


class OntologyMcpAccessSessionValidator(Protocol):
    def require_active(self, ctx: RequestContext, application_id: str) -> None: ...


class OntologyMcpObjectRuntime(Protocol):
    def get(self, object_type_api_name: str, object_id: str, *, ctx: RequestContext | None = None) -> ObjectPayload: ...

    def query(
        self,
        object_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        order_by: Sequence[Mapping[str, str]] | None = None,
        limit: int = 50,
        cursor: str | None = None,
        search_text: str | None = None,
        semantic_text: str | None = None,
    ) -> ObjectQueryResult: ...

    def links(
        self,
        object_type_api_name: str,
        object_id: str,
        link_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> Sequence[Mapping[str, object]]: ...

    def search_around(
        self,
        from_object_type_api_name: str,
        link_types: Sequence[str],
        *,
        ctx: RequestContext | None = None,
        filter_ast: Mapping[str, object] | None = None,
        include_items: bool = True,
    ) -> Mapping[str, object]: ...


class OntologyMcpActionRuntime(Protocol):
    def get(self, action_api_name: str, *, ctx: RequestContext | None = None) -> ActionCatalogItem: ...

    def get_external_mcp(self, action_api_name: str, *, ctx: RequestContext) -> ActionCatalogItem: ...

    def plan(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        branch_id: str | None = None,
        ctx: RequestContext | None = None,
    ) -> ActionExecutionPlanResponse: ...

    def plan_external_mcp(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        ctx: RequestContext,
    ) -> ActionExecutionPlanResponse: ...

    def start_run(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        wait_seconds: int = 0,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...

    def start_external_mcp_run(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        wait_seconds: int = 0,
        ctx: RequestContext,
    ) -> dict[str, object]: ...

    def resume_idempotent_run(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object] | None: ...

    def resume_external_mcp_run(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext,
    ) -> dict[str, object] | None: ...

    def get_run(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]: ...

    def get_external_mcp_run(self, run_id: str, *, ctx: RequestContext) -> dict[str, object]: ...


class OntologyMcpFunctionRuntime(Protocol):
    def describe(self, function_api_name: str, *, ctx: RequestContext | None = None) -> FunctionTypeRow: ...

    def describe_external_mcp(self, function_api_name: str, *, ctx: RequestContext) -> FunctionTypeRow: ...

    def execute(
        self,
        function_api_name: str,
        *,
        inputs: Mapping[str, object],
        ctx: RequestContext | None = None,
    ) -> FunctionExecutionResult: ...

    def execute_external_mcp(
        self,
        function_api_name: str,
        *,
        inputs: Mapping[str, object],
        ctx: RequestContext,
    ) -> FunctionExecutionResult: ...


class OntologyMcpApprovalRuntime(Protocol):
    def has_external_mcp_replay(
        self, ctx: RequestContext, *, application_id: str, action_type: str, idempotency_key: str
    ) -> bool: ...

    def propose_external_mcp(
        self,
        ctx: RequestContext,
        *,
        application_id: str,
        session_id: str,
        json_rpc_id: JsonRpcRequestId,
        action_type: str,
        target_object_type: str,
        target_object_id: str,
        expected_object_version: int,
        parameters: Mapping[str, object],
        execution_plan: ActionExecutionPlanResponse,
        idempotency_key: str,
    ) -> dict[str, object]: ...

    def external_mcp_status(self, ctx: RequestContext, *, application_id: str, review_id: str) -> dict[str, object]: ...
