"""Consumer Ontology MCP gateway over app-restricted OSDK resources."""

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
    OsdkResourceOperation,
    OsdkResourceType,
)
from foundry_lite.application.services.function_execution_service import FunctionExecutionResult
from foundry_lite.application.services.ontology_mcp_tools import (
    action_tool,
    approval_status_tool,
    function_tools,
    object_tools,
    run_status_tool,
)
from foundry_lite.application.services.ontology_mcp_unified_search import (
    OntologyMcpUnifiedSearchRuntime,
    _unified_hit_payload,
)
from foundry_lite.application.services.ontology_mcp_values import (
    ActionRequest as _ActionRequest,
)
from foundry_lite.application.services.ontology_mcp_values import (
    action_description as _action_description,
)
from foundry_lite.application.services.ontology_mcp_values import (
    action_request as _action_request,
)
from foundry_lite.application.services.ontology_mcp_values import (
    bounded_int as _bounded_int,
)
from foundry_lite.application.services.ontology_mcp_values import (
    can_autonomous_apply as _can_autonomous_apply,
)
from foundry_lite.application.services.ontology_mcp_values import (
    grant_name as _grant_name,
)
from foundry_lite.application.services.ontology_mcp_values import (
    grant_scopes as _grant_scopes,
)
from foundry_lite.application.services.ontology_mcp_values import (
    grant_sort_key as _grant_sort_key,
)
from foundry_lite.application.services.ontology_mcp_values import (
    grant_type as _grant_type,
)
from foundry_lite.application.services.ontology_mcp_values import (
    grant_visible as _grant_visible,
)
from foundry_lite.application.services.ontology_mcp_values import (
    mapping as _mapping,
)
from foundry_lite.application.services.ontology_mcp_values import (
    mcp_idempotency_key as _mcp_idempotency_key,
)
from foundry_lite.application.services.ontology_mcp_values import (
    mcp_result as _mcp_result,
)
from foundry_lite.application.services.ontology_mcp_values import (
    optional_mapping as _optional_mapping,
)
from foundry_lite.application.services.ontology_mcp_values import (
    optional_text as _optional_text,
)
from foundry_lite.application.services.ontology_mcp_values import (
    parse_tool_name as _parse_tool_name,
)
from foundry_lite.application.services.ontology_mcp_values import (
    text as _text,
)
from foundry_lite.application.services.ontology_mcp_values import (
    tool_event_payload as _tool_event_payload,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.platform.scopes import resource_scope

JsonObject = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class OntologyMcpToolCall:
    application_id: str
    session_id: str
    json_rpc_id: str
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


class OntologyMcpActionRuntime(Protocol):
    def get(self, action_api_name: str, *, ctx: RequestContext | None = None) -> ActionCatalogItem: ...

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

    def get_run(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]: ...


class OntologyMcpFunctionRuntime(Protocol):
    def describe(self, function_api_name: str, *, ctx: RequestContext | None = None) -> FunctionTypeRow: ...

    def execute(
        self,
        function_api_name: str,
        *,
        inputs: Mapping[str, object],
        ctx: RequestContext | None = None,
    ) -> FunctionExecutionResult: ...


class OntologyMcpApprovalRuntime(Protocol):
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
    ) -> dict[str, object]: ...

    def external_mcp_status(self, ctx: RequestContext, *, application_id: str, review_id: str) -> dict[str, object]: ...


class OntologyMcpGateway:
    """Project and execute only the resources granted to one OAuth application."""

    applications: OntologyMcpApplicationRuntime
    objects: OntologyMcpObjectRuntime
    unified_search: OntologyMcpUnifiedSearchRuntime
    actions: OntologyMcpActionRuntime
    functions: OntologyMcpFunctionRuntime
    approvals: OntologyMcpApprovalRuntime
    access_sessions: OntologyMcpAccessSessionValidator

    def __init__(
        self,
        *,
        applications: OntologyMcpApplicationRuntime,
        objects: OntologyMcpObjectRuntime,
        unified_search: OntologyMcpUnifiedSearchRuntime,
        actions: OntologyMcpActionRuntime,
        functions: OntologyMcpFunctionRuntime,
        approvals: OntologyMcpApprovalRuntime,
        access_sessions: OntologyMcpAccessSessionValidator,
    ) -> None:
        self.applications = applications
        self.objects = objects
        self.unified_search = unified_search
        self.actions = actions
        self.functions = functions
        self.approvals = approvals
        self.access_sessions = access_sessions

    def list_tools(self, ctx: RequestContext, application_id: str, *, origin: str | None = None) -> dict[str, object]:
        grants = self._grants(ctx, application_id, origin=origin)
        tools: list[dict[str, object]] = []
        for grant in sorted(grants, key=_grant_sort_key):
            tools.extend(self._tools_for_grant(ctx, grant))
        if any(_grant_type(grant) == "action" for grant in grants):
            tools.append(run_status_tool())
            tools.append(approval_status_tool())
        return {"tools": tools}

    def open_session(
        self, ctx: RequestContext, application_id: str, session_id: str, *, origin: str | None = None
    ) -> Mapping[str, object]:
        self.access_sessions.require_active(ctx, application_id)
        return self.applications.open_mcp_session(ctx, application_id, session_id, origin=origin)

    def session_events(
        self, ctx: RequestContext, application_id: str, session_id: str, *, after_sequence: int = 0
    ) -> list[OsdkMcpSessionEventRow]:
        self.access_sessions.require_active(ctx, application_id)
        return self.applications.list_mcp_session_events(ctx, application_id, session_id, after_sequence=after_sequence)

    def close_session(self, ctx: RequestContext, application_id: str, session_id: str) -> Mapping[str, object]:
        self.access_sessions.require_active(ctx, application_id)
        return self.applications.close_mcp_session(ctx, application_id, session_id)

    def execute_tool(self, ctx: RequestContext, call: OntologyMcpToolCall) -> dict[str, object]:
        self._grants(ctx, call.application_id, origin=call.origin)
        tool_kind, resource_name, operation = _parse_tool_name(call.tool_name)
        if tool_kind == "object":
            result = self._execute_object(ctx, resource_name, operation, call.arguments)
        elif tool_kind == "action":
            result = self._execute_action(ctx, call, resource_name, operation)
        elif tool_kind == "function":
            result = self._execute_function(ctx, resource_name, operation, call.arguments)
        elif tool_kind == "action_run":
            result = self._execute_run_status(ctx, operation, call.arguments)
        else:
            result = self._execute_approval_status(ctx, call, operation)
        self.applications.record_mcp_session_event(
            ctx,
            call.application_id,
            call.session_id,
            event_type="tool.completed",
            payload=_tool_event_payload(call, result),
        )
        return _mcp_result(result)

    def _grants(
        self, ctx: RequestContext, application_id: str, *, origin: str | None = None
    ) -> list[Mapping[str, object]]:
        self.access_sessions.require_active(ctx, application_id)
        self.applications.require_mcp_enabled(ctx, application_id, origin=origin)
        rows = self.applications.runtime_resource_grants(ctx, application_id=application_id)
        return [row for row in rows if _grant_visible(ctx, row)]

    def _tools_for_grant(self, ctx: RequestContext, grant: Mapping[str, object]) -> list[dict[str, object]]:
        resource_type = _grant_type(grant)
        name = _grant_name(grant)
        scopes = _grant_scopes(grant)
        if resource_type == "object":
            return object_tools(name, scopes)
        if resource_type == "action":
            return self._action_tools(ctx, name, scopes)
        if resource_type == "function":
            function = self.functions.describe(name, ctx=ctx)
            return function_tools(name, scopes, function["definition"])
        return []

    def _action_tools(self, ctx: RequestContext, name: str, scopes: tuple[str, ...]) -> list[dict[str, object]]:
        item = self.actions.get(name, ctx=ctx)
        schema = dict(item["parameterSchema"])
        description = _action_description(item)
        tools: list[dict[str, object]] = []
        validate_scope = resource_scope("action", name, "validate")
        execute_scope = resource_scope("action", name, "execute")
        if validate_scope in scopes:
            tools.append(action_tool(name, "plan", description, schema, is_write=False))
        if validate_scope in scopes and execute_scope in scopes:
            tools.append(action_tool(name, "apply", description, schema, is_write=True))
        return tools

    def _execute_object(
        self,
        ctx: RequestContext,
        name: str,
        operation: str,
        arguments: JsonObject,
    ) -> Mapping[str, object]:
        self._require_scope(ctx, "object", name, "read")
        if operation == "get":
            return self.objects.get(name, _text(arguments, "objectId"), ctx=ctx)
        if operation == "search":
            return self.objects.query(
                name,
                ctx=ctx,
                filter_ast=_optional_mapping(arguments.get("filter")),
                limit=_bounded_int(arguments.get("limit"), 20, 1, 50),
                cursor=_optional_text(arguments.get("cursor")),
                search_text=_optional_text(arguments.get("search")),
                # The runtime has always accepted this; only the MCP surface withheld it, so an
                # external agent could keyword-match but never search by meaning.
                semantic_text=_optional_text(arguments.get("semanticText")),
            )
        if operation == "unifiedSearch":
            hits = self.unified_search.unified_search(
                ctx,
                query_text=_text(arguments, "query"),
                object_type=name,
                filters=_optional_mapping(arguments.get("filter")),
                limit=_bounded_int(arguments.get("limit"), 20, 1, 50),
            )
            return {"hits": [_unified_hit_payload(hit) for hit in hits]}
        raise ValidationFailed("unsupported Ontology MCP object operation")

    def _execute_action(
        self,
        ctx: RequestContext,
        call: OntologyMcpToolCall,
        name: str,
        operation: str,
    ) -> Mapping[str, object]:
        self._require_scope(ctx, "action", name, "validate")
        if operation == "apply":
            self._require_scope(ctx, "action", name, "execute")
        elif operation != "plan":
            raise ValidationFailed("unsupported Ontology MCP action operation")
        args = call.arguments
        request = _action_request(args)
        idempotency_key = _mcp_idempotency_key(ctx, call)
        if operation == "apply":
            replay = self.actions.resume_idempotent_run(name, **request, idempotency_key=idempotency_key, ctx=ctx)
            if replay is not None:
                return replay
        plan = self.actions.plan(
            name,
            **request,
            ctx=ctx,
        )
        if operation == "plan":
            return plan
        return self._apply_action(ctx, call, name, request, plan, idempotency_key)

    def _apply_action(
        self,
        ctx: RequestContext,
        call: OntologyMcpToolCall,
        name: str,
        request: _ActionRequest,
        plan: ActionExecutionPlanResponse,
        idempotency_key: str,
    ) -> Mapping[str, object]:
        item = self.actions.get(name, ctx=ctx)
        if not _can_autonomous_apply(item, plan):
            return self.approvals.propose_external_mcp(
                ctx,
                application_id=call.application_id,
                session_id=call.session_id,
                json_rpc_id=call.json_rpc_id,
                action_type=name,
                target_object_type=str(request["object_type"]),
                target_object_id=str(request["object_id"]),
                expected_object_version=int(request["expected_object_version"]),
                parameters=_mapping(request["params"], "params"),
                execution_plan=plan,
                idempotency_key=idempotency_key,
            )
        return self.actions.start_run(
            name,
            **request,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def _execute_function(
        self, ctx: RequestContext, name: str, operation: str, arguments: JsonObject
    ) -> Mapping[str, object]:
        if operation != "execute":
            raise ValidationFailed("unsupported Ontology MCP function operation")
        self._require_scope(ctx, "function", name, "execute")
        return self.functions.execute(name, inputs=_mapping(arguments.get("inputs"), "inputs"), ctx=ctx)

    def _execute_run_status(self, ctx: RequestContext, operation: str, arguments: JsonObject) -> Mapping[str, object]:
        if operation != "get":
            raise ValidationFailed("unsupported Ontology MCP run operation")
        run = self.actions.get_run(_text(arguments, "runId"), ctx=ctx)
        action_name = _text(run, "actionApiName")
        self._require_scope(ctx, "action", action_name, "execute")
        return run

    def _execute_approval_status(
        self, ctx: RequestContext, call: OntologyMcpToolCall, operation: str
    ) -> Mapping[str, object]:
        if operation != "get":
            raise ValidationFailed("unsupported Ontology MCP approval operation")
        result = self.approvals.external_mcp_status(
            ctx, application_id=call.application_id, review_id=_text(call.arguments, "reviewId")
        )
        self._require_scope(ctx, "action", _text(result, "actionApiName"), "execute")
        return result

    def _require_scope(
        self,
        ctx: RequestContext,
        resource_type: OsdkResourceType,
        name: str,
        operation: OsdkResourceOperation,
    ) -> None:
        self.applications.require_resource_scope(
            ctx,
            resource_type=resource_type,
            resource_api_name=name,
            operation=operation,
        )
