"""Consumer Ontology MCP gateway over app-restricted OSDK resources."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.action_types import ActionCatalogItem, ActionExecutionPlanResponse
from foundry_lite.application.ports import (
    OsdkMcpSessionEventRow,
    OsdkMcpStreamLease,
    OsdkResourceOperation,
    OsdkResourceType,
)
from foundry_lite.application.services.mcp_rate_limit_service import McpRateLimitService
from foundry_lite.application.services.mcp_tool_results import tool_error_result
from foundry_lite.application.services.ontology_mcp_contracts import (
    OntologyMcpAccessSessionValidator,
    OntologyMcpActionRuntime,
    OntologyMcpApplicationRuntime,
    OntologyMcpApprovalRuntime,
    OntologyMcpFunctionRuntime,
    OntologyMcpObjectRuntime,
    OntologyMcpToolCall,
    require_ontology_mcp_session_namespace,
)
from foundry_lite.application.services.ontology_mcp_schema import validate_tool_arguments
from foundry_lite.application.services.ontology_mcp_tools import (
    action_tool,
    approval_status_tool,
    function_tools,
    object_tools,
    run_status_tool,
)
from foundry_lite.application.services.ontology_mcp_unified_search import (
    OntologyMcpUnifiedSearchRuntime,
    execute_object_tool,
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
    can_autonomous_apply as _can_autonomous_apply,
)
from foundry_lite.application.services.ontology_mcp_values import (
    effective_grant_scopes as _effective_grant_scopes,
)
from foundry_lite.application.services.ontology_mcp_values import (
    grant_name as _grant_name,
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
    parse_tool_name as _parse_tool_name,
)
from foundry_lite.application.services.ontology_mcp_values import (
    text as _text,
)
from foundry_lite.application.services.ontology_mcp_values import (
    tool_event_payload as _tool_event_payload,
)
from foundry_lite.application.services.osdk_service_principal_authorization import (
    is_client_credentials_service_principal,
    require_service_principal_scope,
    service_principal_reader_context,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, ValidationFailed
from foundry_lite.domain.platform.scopes import resource_scope

JsonObject = Mapping[str, object]


class OntologyMcpGateway:
    """Project and execute only the resources granted to one OAuth application."""

    applications: OntologyMcpApplicationRuntime
    objects: OntologyMcpObjectRuntime
    unified_search: OntologyMcpUnifiedSearchRuntime
    actions: OntologyMcpActionRuntime
    functions: OntologyMcpFunctionRuntime
    approvals: OntologyMcpApprovalRuntime
    access_sessions: OntologyMcpAccessSessionValidator
    rate_limits: McpRateLimitService

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
        rate_limits: McpRateLimitService,
    ) -> None:
        self.applications = applications
        self.objects = objects
        self.unified_search = unified_search
        self.actions = actions
        self.functions = functions
        self.approvals = approvals
        self.access_sessions = access_sessions
        self.rate_limits = rate_limits

    def consume_endpoint_rate_limit(self, ctx: RequestContext, application_id: str) -> None:
        self.rate_limits.consume_endpoint(ctx, plane="ontology", application_id=application_id)

    def list_tools(self, ctx: RequestContext, application_id: str, *, origin: str | None = None) -> dict[str, object]:
        grants = self._grants(ctx, application_id, origin=origin)
        effective_scopes = frozenset(scope for grant in grants for scope in _effective_grant_scopes(ctx, grant))
        tools: list[dict[str, object]] = []
        for grant in sorted(grants, key=_grant_sort_key):
            tools.extend(self._tools_for_grant(ctx, grant, effective_scopes))
        if any(_is_action_apply_tool(tool) for tool in tools):
            tools.append(run_status_tool())
            tools.append(approval_status_tool())
        return {"tools": tools}

    def open_session(
        self, ctx: RequestContext, application_id: str, session_id: str, *, origin: str | None = None
    ) -> Mapping[str, object]:
        require_ontology_mcp_session_namespace(session_id)
        self.access_sessions.require_active(ctx, application_id)
        return self.applications.open_mcp_session(ctx, application_id, session_id, origin=origin)

    def resume_session(
        self, ctx: RequestContext, application_id: str, session_id: str, *, origin: str | None = None
    ) -> Mapping[str, object]:
        require_ontology_mcp_session_namespace(session_id)
        self.access_sessions.require_active(ctx, application_id)
        return self.applications.resume_mcp_session(ctx, application_id, session_id, origin=origin)

    def claim_session_stream(
        self, ctx: RequestContext, application_id: str, session_id: str, *, origin: str | None = None
    ) -> OsdkMcpStreamLease:
        require_ontology_mcp_session_namespace(session_id)
        self.access_sessions.require_active(ctx, application_id)
        return self.applications.claim_mcp_session_stream(ctx, application_id, session_id, origin=origin)

    def release_session_stream(self, ctx: RequestContext, application_id: str, session_id: str, lease_id: str) -> bool:
        require_ontology_mcp_session_namespace(session_id)
        return self.applications.release_mcp_session_stream(ctx, application_id, session_id, lease_id)

    def session_events(
        self, ctx: RequestContext, application_id: str, session_id: str, *, after_sequence: int = 0
    ) -> list[OsdkMcpSessionEventRow]:
        require_ontology_mcp_session_namespace(session_id)
        self.access_sessions.require_active(ctx, application_id)
        return self.applications.list_mcp_session_events(ctx, application_id, session_id, after_sequence=after_sequence)

    def close_session(self, ctx: RequestContext, application_id: str, session_id: str) -> Mapping[str, object]:
        require_ontology_mcp_session_namespace(session_id)
        self.access_sessions.require_active(ctx, application_id)
        return self.applications.close_mcp_session(ctx, application_id, session_id)

    def execute_tool(self, ctx: RequestContext, call: OntologyMcpToolCall) -> dict[str, object]:
        require_ontology_mcp_session_namespace(call.session_id)
        listed = self.list_tools(ctx, call.application_id, origin=call.origin)
        validate_tool_arguments(call.arguments, _tool_input_schema(listed, call.tool_name))
        tool_kind, resource_name, operation = _parse_tool_name(call.tool_name)
        try:
            if tool_kind != "action" or operation != "apply":
                self.rate_limits.consume_tool(ctx, plane="ontology", application_id=call.application_id)
            result = self._execute_known_tool(ctx, call, tool_kind, resource_name, operation)
        except FoundryLiteError as exc:
            self._record_completed(ctx, call, {"status": "failed", "errorType": exc.code})
            return tool_error_result(exc, request_id=ctx.request_id)
        self._record_completed(ctx, call, result)
        return _mcp_result(result)

    def _execute_known_tool(
        self,
        ctx: RequestContext,
        call: OntologyMcpToolCall,
        tool_kind: str,
        resource_name: str,
        operation: str,
    ) -> Mapping[str, object]:
        if tool_kind == "object":
            return self._execute_object(ctx, resource_name, operation, call.arguments)
        if tool_kind == "action":
            return self._execute_action(ctx, call, resource_name, operation)
        if tool_kind == "function":
            return self._execute_function(ctx, resource_name, operation, call.arguments)
        if tool_kind == "action_run":
            return self._execute_run_status(ctx, operation, call.arguments)
        return self._execute_approval_status(ctx, call, operation)

    def _record_completed(self, ctx: RequestContext, call: OntologyMcpToolCall, result: Mapping[str, object]) -> None:
        self.applications.record_mcp_session_event(
            ctx,
            call.application_id,
            call.session_id,
            event_type="notifications/tool.completed",
            payload=_tool_event_payload(call, result),
        )

    def _grants(
        self, ctx: RequestContext, application_id: str, *, origin: str | None = None
    ) -> list[Mapping[str, object]]:
        self.access_sessions.require_active(ctx, application_id)
        self.applications.require_mcp_enabled(ctx, application_id, origin=origin)
        rows = self.applications.runtime_resource_grants(ctx, application_id=application_id)
        return [row for row in rows if _grant_visible(ctx, row)]

    def _tools_for_grant(
        self, ctx: RequestContext, grant: Mapping[str, object], effective_scopes: frozenset[str]
    ) -> list[dict[str, object]]:
        resource_type = _grant_type(grant)
        name = _grant_name(grant)
        scopes = _effective_grant_scopes(ctx, grant)
        if resource_type == "object":
            return object_tools(name, scopes)
        if resource_type == "action":
            return self._action_tools(ctx, name, scopes, effective_scopes)
        if resource_type == "function":
            function = (
                self.functions.describe_external_mcp(name, ctx=ctx)
                if is_client_credentials_service_principal(ctx)
                else self.functions.describe(name, ctx=ctx)
            )
            return function_tools(name, scopes, function["definition"])
        return []

    def _action_tools(
        self, ctx: RequestContext, name: str, scopes: tuple[str, ...], effective_scopes: frozenset[str]
    ) -> list[dict[str, object]]:
        item = (
            self.actions.get_external_mcp(name, ctx=ctx)
            if is_client_credentials_service_principal(ctx)
            else self.actions.get(name, ctx=ctx)
        )
        schema = dict(item["parameterSchema"])
        description = _action_description(item)
        target_schema = _action_target_input_schema(item)
        tools: list[dict[str, object]] = []
        validate_scope = resource_scope("action", name, "validate")
        execute_scope = resource_scope("action", name, "execute")
        has_target_read = _has_action_target_read(item, effective_scopes)
        if validate_scope in scopes and has_target_read:
            tools.append(action_tool(name, "plan", description, target_schema, schema, is_write=False))
        if validate_scope in scopes and execute_scope in scopes and has_target_read:
            tools.append(action_tool(name, "apply", description, target_schema, schema, is_write=True))
        return tools

    def _execute_object(
        self,
        ctx: RequestContext,
        name: str,
        operation: str,
        arguments: JsonObject,
    ) -> Mapping[str, object]:
        return execute_object_tool(
            objects=self.objects,
            unified_search=self.unified_search,
            require_object_read=self._require_object_read,
            object_context=self._object_context,
            ctx=ctx,
            name=name,
            operation=operation,
            arguments=arguments,
        )

    def _require_object_read(self, ctx: RequestContext, name: str) -> None:
        self._require_scope(ctx, "object", name, "read")

    def _object_context(self, ctx: RequestContext, name: str) -> RequestContext:
        if not is_client_credentials_service_principal(ctx):
            return ctx
        require_service_principal_scope(
            ctx,
            self.access_sessions,
            self.applications,
            resource_type="object",
            resource_api_name=name,
            operation="read",
        )
        return service_principal_reader_context(ctx)

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
            replay = self._resume_action(ctx, name, request, idempotency_key)
            if replay is not None:
                return replay
            if self.approvals.has_external_mcp_replay(
                ctx, application_id=call.application_id, action_type=name, idempotency_key=idempotency_key
            ):
                plan = self._plan_action(ctx, name, request)
                return self._apply_action(ctx, call, name, request, plan, idempotency_key, is_proposal_replay=True)
            self.rate_limits.consume_tool(ctx, plane="ontology", application_id=call.application_id)
        plan = self._plan_action(ctx, name, request)
        if operation == "plan":
            return plan
        return self._apply_action(ctx, call, name, request, plan, idempotency_key)

    def _resume_action(
        self,
        ctx: RequestContext,
        name: str,
        request: _ActionRequest,
        idempotency_key: str,
    ) -> Mapping[str, object] | None:
        if is_client_credentials_service_principal(ctx):
            return self.actions.resume_external_mcp_run(name, **request, idempotency_key=idempotency_key, ctx=ctx)
        return self.actions.resume_idempotent_run(name, **request, idempotency_key=idempotency_key, ctx=ctx)

    def _plan_action(self, ctx: RequestContext, name: str, request: _ActionRequest) -> ActionExecutionPlanResponse:
        if is_client_credentials_service_principal(ctx):
            return self.actions.plan_external_mcp(name, **request, ctx=ctx)
        return self.actions.plan(name, **request, ctx=ctx)

    def _apply_action(
        self,
        ctx: RequestContext,
        call: OntologyMcpToolCall,
        name: str,
        request: _ActionRequest,
        plan: ActionExecutionPlanResponse,
        idempotency_key: str,
        *,
        is_proposal_replay: bool = False,
    ) -> Mapping[str, object]:
        item = (
            self.actions.get_external_mcp(name, ctx=ctx)
            if is_client_credentials_service_principal(ctx)
            else self.actions.get(name, ctx=ctx)
        )
        if is_proposal_replay or not _can_autonomous_apply(item, plan):
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
        if is_client_credentials_service_principal(ctx):
            return self.actions.start_external_mcp_run(name, **request, idempotency_key=idempotency_key, ctx=ctx)
        return self.actions.start_run(name, **request, idempotency_key=idempotency_key, ctx=ctx)

    def _execute_function(
        self, ctx: RequestContext, name: str, operation: str, arguments: JsonObject
    ) -> Mapping[str, object]:
        if operation != "execute":
            raise ValidationFailed("unsupported Ontology MCP function operation")
        self._require_scope(ctx, "function", name, "execute")
        if is_client_credentials_service_principal(ctx):
            return self.functions.execute_external_mcp(
                name, inputs=_mapping(arguments.get("inputs"), "inputs"), ctx=ctx
            )
        return self.functions.execute(name, inputs=_mapping(arguments.get("inputs"), "inputs"), ctx=ctx)

    def _execute_run_status(self, ctx: RequestContext, operation: str, arguments: JsonObject) -> Mapping[str, object]:
        if operation != "get":
            raise ValidationFailed("unsupported Ontology MCP run operation")
        run_id = _text(arguments, "runId")
        run = (
            self.actions.get_external_mcp_run(run_id, ctx=ctx)
            if is_client_credentials_service_principal(ctx)
            else self.actions.get_run(run_id, ctx=ctx)
        )
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


def _tool_input_schema(listed: Mapping[str, object], tool_name: str) -> Mapping[str, object]:
    tools = listed.get("tools")
    if isinstance(tools, Sequence) and not isinstance(tools, str | bytes):
        for tool in tools:
            if not isinstance(tool, Mapping) or tool.get("name") != tool_name:
                continue
            schema = tool.get("inputSchema")
            if isinstance(schema, Mapping):
                return schema
    raise ValidationFailed("Ontology MCP tool is not available")


def _action_target_input_schema(item: ActionCatalogItem) -> dict[str, object]:
    schema: dict[str, object] = {"type": "string", "pattern": r"\S"}
    target = item["target"]
    if target.get("kind") == "object" and isinstance(target.get("apiName"), str):
        schema["const"] = target["apiName"]
    return schema


def _has_action_target_read(item: ActionCatalogItem, scopes: frozenset[str]) -> bool:
    target = item["target"]
    api_name = target.get("apiName")
    if target.get("kind") == "object" and isinstance(api_name, str):
        return resource_scope("object", api_name, "read") in scopes
    return any(scope.startswith("osdk:object:") and scope.endswith(":read") for scope in scopes)


def _is_action_apply_tool(tool: Mapping[str, object]) -> bool:
    name = str(tool.get("name", ""))
    return name.startswith("action.") and name.endswith(".apply")
