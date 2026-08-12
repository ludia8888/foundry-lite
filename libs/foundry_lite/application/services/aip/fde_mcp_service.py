"""Builder MCP catalog and direct governed tool execution with durable AI evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from foundry_lite.application.ports import (
    AiRunRepository,
    OsdkApplicationRepository,
    OsdkMcpSessionEventRow,
    OsdkMcpStreamLease,
    TransactionManager,
)
from foundry_lite.application.services.aip.fde_catalog import FDE_MODES, fde_tool_catalog
from foundry_lite.application.services.aip.fde_mcp_contract import (
    FdeMcpRequestBinding,
    FdeOntologyToolError,
    FdePlatformToolError,
    ToolSpec,
    is_scope_allowed,
    tool_error_result,
    validate_outer_shape,
)
from foundry_lite.application.services.aip.fde_mcp_contract import (
    call_binding as _binding,
)
from foundry_lite.application.services.aip.fde_mcp_contract import (
    error_result_payload as _error_result_payload,
)
from foundry_lite.application.services.aip.fde_mcp_contract import (
    guard_external_tool as _guard_external_tool,
)
from foundry_lite.application.services.aip.fde_mcp_contract import (
    has_active_client as _has_active_client,
)
from foundry_lite.application.services.aip.fde_mcp_contract import (
    mcp_run_id as _mcp_run_id,
)
from foundry_lite.application.services.aip.fde_mcp_contract import (
    mcp_tool as _mcp_tool,
)
from foundry_lite.application.services.aip.fde_mcp_contract import (
    mode_scope as _mode_scope,
)
from foundry_lite.application.services.aip.fde_mcp_contract import (
    platform_request as _platform_request,
)
from foundry_lite.application.services.aip.fde_mcp_contract import (
    replay_result_payload as _replay_result_payload,
)
from foundry_lite.application.services.aip.fde_mcp_contract import (
    result_payload as _result_payload,
)
from foundry_lite.application.services.aip.fde_mcp_contract import (
    tool_domain_error as _tool_domain_error,
)
from foundry_lite.application.services.aip.fde_mcp_contract import (
    tool_input_schema as _tool_input_schema,
)
from foundry_lite.application.services.aip.fde_mcp_contract import (
    tool_spec as _tool_spec,
)
from foundry_lite.application.services.aip.fde_mcp_contract import (
    validated_outer_input as _validated_outer_input,
)
from foundry_lite.application.services.aip.fde_mcp_discovery import (
    activate_tools as _activate_tools,
)
from foundry_lite.application.services.aip.fde_mcp_discovery import (
    allowed_modes as _allowed_modes,
)
from foundry_lite.application.services.aip.fde_mcp_discovery import (
    is_search as _is_search,
)
from foundry_lite.application.services.aip.fde_mcp_discovery import (
    mcp_search_tool as _mcp_search_tool,
)
from foundry_lite.application.services.aip.fde_mcp_discovery import (
    rank_tools as _rank_tools,
)
from foundry_lite.application.services.aip.fde_mcp_discovery import (
    required_search_query as _required_search_query,
)
from foundry_lite.application.services.aip.fde_mcp_discovery import (
    search_ledger as _search_ledger,
)
from foundry_lite.application.services.aip.fde_mcp_discovery import (
    search_limit as _search_limit,
)
from foundry_lite.application.services.aip.fde_mcp_run_ledger import FdeMcpRunLedger
from foundry_lite.application.services.aip.fde_mcp_security import FdeMcpSecurityLedger
from foundry_lite.application.services.aip.fde_mcp_sessions import LAZY_DISCOVERY_MARKER, FdeMcpSessionLedger
from foundry_lite.application.services.aip.fde_mcp_types import (
    FdeMcpAccessSessionValidator,
    FdeMcpApplicationReader,
    FdeMcpContextValidator,
    FdeMcpPlatformExecutor,
    FdeMcpToolCall,
)
from foundry_lite.application.services.mcp_rate_limit_service import McpRateLimitService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, PermissionDenied, RateLimited, ValidationFailed
from foundry_lite.security.policy import PolicyService

JsonObject = Mapping[str, object]


class FdeMcpGateway:
    """External MCP integration boundary over governed application services."""

    access_session_validator: FdeMcpAccessSessionValidator
    run_ledger: FdeMcpRunLedger
    rate_limits: McpRateLimitService
    security_ledger: FdeMcpSecurityLedger
    session_ledger: FdeMcpSessionLedger

    def __init__(
        self,
        *,
        engine: TransactionManager,
        policy: PolicyService,
        ai_run_repository: AiRunRepository,
        context_validator: FdeMcpContextValidator,
        platform_executor: FdeMcpPlatformExecutor,
        application_reader: FdeMcpApplicationReader,
        application_repository: OsdkApplicationRepository,
        access_session_validator: FdeMcpAccessSessionValidator,
        rate_limits: McpRateLimitService,
    ) -> None:
        self.engine = engine
        self.policy = policy
        self.ai_run_repository = ai_run_repository
        self.fde_context_service = context_validator
        self.fde_platform_tool_service = platform_executor
        self.osdk_application_service = application_reader
        self.osdk_application_repository = application_repository
        self.access_session_validator = access_session_validator
        self.rate_limits = rate_limits
        self.session_ledger = FdeMcpSessionLedger(engine, application_repository)
        self.security_ledger = FdeMcpSecurityLedger(engine, ai_run_repository, policy)
        self.run_ledger = FdeMcpRunLedger(engine, ai_run_repository, self.security_ledger)

    def consume_endpoint_rate_limit(self, ctx: RequestContext, application_id: str) -> None:
        self.rate_limits.consume_endpoint(ctx, plane="builder", application_id=application_id)

    def open_session(
        self,
        ctx: RequestContext,
        application_id: str,
        session_id: str,
    ) -> Mapping[str, object]:
        self._authorized_application_bundle(ctx, application_id)
        return self.session_ledger.open(ctx, application_id, session_id)

    def session_events(
        self,
        ctx: RequestContext,
        application_id: str,
        session_id: str,
        *,
        after_sequence: int = 0,
    ) -> list[OsdkMcpSessionEventRow]:
        self._authorized_application_bundle(ctx, application_id)
        return self.session_ledger.events(
            ctx,
            application_id,
            session_id,
            after_sequence=after_sequence,
        )

    def claim_session_stream(self, ctx: RequestContext, application_id: str, session_id: str) -> OsdkMcpStreamLease:
        self._authorized_application_bundle(ctx, application_id)
        return self.session_ledger.claim_stream(ctx, application_id, session_id)

    def release_session_stream(self, ctx: RequestContext, application_id: str, session_id: str, lease_id: str) -> bool:
        return self.session_ledger.release_stream(ctx, application_id, session_id, lease_id)

    def close_session(
        self,
        ctx: RequestContext,
        application_id: str,
        session_id: str,
    ) -> Mapping[str, object]:
        self._authorized_application_bundle(ctx, application_id)
        return self.session_ledger.close(ctx, application_id, session_id)

    def approve_confirmation(
        self,
        ctx: RequestContext,
        application_id: str,
        challenge_id: str,
    ) -> Mapping[str, object]:
        bundle = self.osdk_application_service.get_application(application_id, ctx=ctx)
        application = bundle.get("application")
        if not isinstance(application, Mapping) or application.get("status") != "active":
            raise PermissionDenied("Builder MCP application is not active")
        return self.security_ledger.approve(ctx, application_id, challenge_id)

    def approve_widget_confirmation(
        self,
        ctx: RequestContext,
        application_id: str,
        session_id: str,
        challenge_id: str,
        widget_approval_token: str,
        origin: str | None,
    ) -> Mapping[str, object]:
        self._authorized_application_bundle(ctx, application_id)
        self.session_ledger.require_active(ctx, application_id, session_id)
        is_recovery = self.security_ledger.is_widget_approval_recovery(
            ctx, application_id, session_id, challenge_id, widget_approval_token, origin
        )
        if not is_recovery:
            try:
                self.rate_limits.consume_tool(ctx, plane="builder", application_id=application_id)
            except RateLimited as exc:
                return tool_error_result(exc, request_id=ctx.request_id)
        return self.security_ledger.approve_widget(
            ctx, application_id, session_id, challenge_id, widget_approval_token, origin
        )

    def list_tools(
        self,
        ctx: RequestContext,
        application_id: str,
        *,
        session_id: str | None = None,
        discovery_mode: str = "eager",
    ) -> dict[str, object]:
        bundle = self._authorized_application_bundle(ctx, application_id)
        if session_id is not None:
            self.session_ledger.require_active(ctx, application_id, session_id)
        allowed = self._allowed_tools(ctx, bundle)
        search = _mcp_search_tool(_allowed_modes(allowed))
        if discovery_mode == "eager":
            projected = [
                _mcp_tool(tuple(sorted(modes)), tool) for tool, modes in allowed.values() if not _is_search(tool)
            ]
            return {"tools": [search, *projected], "discoveryMode": "eager"}
        if discovery_mode != "lazy" or not session_id:
            raise ValidationFailed("Builder MCP discoveryMode must be eager or lazy with a session")
        activated = self._activated_tool_ids(ctx, application_id, session_id)
        projected = [
            _mcp_tool(tuple(sorted(modes)), tool)
            for tool, modes in allowed.values()
            if tool.tool_id in activated and not _is_search(tool)
        ]
        return {"tools": [search, *projected], "discoveryMode": "lazy", "activatedToolCount": len(projected)}

    def activate_lazy_discovery(self, ctx: RequestContext, application_id: str, session_id: str) -> None:
        self._authorized_application_bundle(ctx, application_id)
        self.session_ledger.mark_lazy(ctx, application_id, session_id)

    def _allowed_tools(self, ctx: RequestContext, bundle: JsonObject) -> dict[str, tuple[ToolSpec, set[str]]]:
        allowed: dict[str, tuple[ToolSpec, set[str]]] = {}
        for mode in FDE_MODES:
            if self._mode_allowed(ctx, bundle, mode.mode_id):
                for tool in fde_tool_catalog(mode.mode_id, ()):
                    if self.policy.decide(ctx, tool.required_permission).allowed:
                        allowed.setdefault(tool.tool_id, (tool, set()))[1].add(mode.mode_id)
        return dict(sorted(allowed.items()))

    def execute_tool(self, ctx: RequestContext, request: FdeMcpToolCall) -> dict[str, object]:
        catalog = self._authorized_catalog(ctx, request)
        is_search = request.tool_id in {"search_tools", "fde.tools.search"}
        spec = _tool_spec(catalog, "fde.tools.search" if is_search else request.tool_id)
        schema = (
            _mcp_search_tool((request.mode,))["inputSchema"] if is_search else _tool_input_schema((request.mode,), spec)
        )
        if not isinstance(schema, Mapping):
            raise ValidationFailed("Builder MCP tool input schema is invalid")
        validate_outer_shape(request, schema)
        run_id = _mcp_run_id(ctx, request)
        replay = self.security_ledger.replay(ctx, run_id, _binding(ctx, request, spec))
        if replay is not None:
            return _replay_result_payload(run_id, replay)
        try:
            self.rate_limits.consume_tool(ctx, plane="builder", application_id=request.application_id)
        except RateLimited as exc:
            return tool_error_result(exc, request_id=ctx.request_id)
        self.fde_context_service.validate_scope(ctx, request.mode, request.workspace_ref)
        if is_search:
            return self._execute_tool_search(ctx, self._validated_search_request(request), catalog)
        request = self._validated_request(request, schema)
        _guard_external_tool(spec)
        self._require_lazy_activation(ctx, request, spec)
        return self._execute_catalog_tool(ctx, request, catalog, spec)

    def _authorized_catalog(
        self,
        ctx: RequestContext,
        request: FdeMcpToolCall,
    ) -> tuple[ToolSpec, ...]:
        bundle = self._authorized_application_bundle(ctx, request.application_id)
        self.session_ledger.require_active(ctx, request.application_id, request.session_id)
        if not self._mode_allowed(ctx, bundle, request.mode):
            raise PermissionDenied("Builder MCP mode is outside application restrictions")
        return fde_tool_catalog(request.mode, ())

    def _validated_search_request(self, request: FdeMcpToolCall) -> FdeMcpToolCall:
        search_schema = _mcp_search_tool((request.mode,))["inputSchema"]
        if not isinstance(search_schema, Mapping):
            raise ValidationFailed("Builder MCP search_tools input schema is invalid")
        return self._validated_request(request, search_schema)

    def _execute_catalog_tool(
        self,
        ctx: RequestContext,
        request: FdeMcpToolCall,
        catalog: tuple[ToolSpec, ...],
        spec: ToolSpec,
    ) -> dict[str, object]:
        run_id = _mcp_run_id(ctx, request)
        binding = _binding(ctx, request, spec)
        early_result = self._replay_or_confirm(ctx, request, spec, run_id, binding)
        if early_result is not None:
            return early_result
        claimed = self._claim_run(ctx, request, binding, run_id, catalog)
        if claimed is not None:
            return claimed
        return self._invoke_tool(ctx, request, catalog, spec, run_id)

    def _invoke_tool(
        self,
        ctx: RequestContext,
        request: FdeMcpToolCall,
        catalog: tuple[ToolSpec, ...],
        spec: ToolSpec,
        run_id: str,
    ) -> dict[str, object]:
        try:
            result = self.fde_platform_tool_service.execute(
                ctx,
                _platform_request(request, run_id, spec, catalog, is_confirmed=spec.effect != "READ"),
            )
        except (FdePlatformToolError, FdeOntologyToolError) as exc:
            domain_error = _tool_domain_error(exc)
            self.security_ledger.fail_execution(ctx, run_id, domain_error)
            return _error_result_payload(
                run_id,
                f"{run_id}-tool-1",
                domain_error,
                request_id=ctx.request_id,
            )
        except FoundryLiteError as exc:
            self.security_ledger.fail_execution(ctx, run_id, exc)
            return _error_result_payload(run_id, f"{run_id}-tool-1", exc, request_id=ctx.request_id)
        except Exception as exc:
            self.security_ledger.fail_execution(ctx, run_id, exc)
            raise
        self.run_ledger.complete(ctx, run_id, result.ledger_record)
        self.session_ledger.record_tool_completed(
            ctx, request.application_id, request.session_id, request.tool_id, run_id
        )
        return _result_payload(run_id, result.tool_call_id, result.output_json, is_replayed=False)

    def _execute_tool_search(
        self, ctx: RequestContext, request: FdeMcpToolCall, catalog: tuple[ToolSpec, ...]
    ) -> dict[str, object]:
        query = _required_search_query(request.arguments)
        limit = _search_limit(request.arguments.get("maxResults"))
        candidates = tuple(
            tool
            for tool in catalog
            if not _is_search(tool) and self.policy.decide(ctx, tool.required_permission).allowed
        )
        matches = _rank_tools(query, candidates, limit)
        run_id = _mcp_run_id(ctx, request)
        binding = _binding(ctx, request, _tool_spec(catalog, "fde.tools.search"))
        replay = self.security_ledger.replay(ctx, run_id, binding)
        if replay is not None:
            return _replay_result_payload(run_id, replay)
        claimed = self._claim_run(ctx, request, binding, run_id, catalog)
        if claimed is not None:
            return claimed
        output = _activate_tools(
            self.engine,
            self.osdk_application_repository,
            self.session_ledger,
            ctx,
            request,
            query,
            matches,
        )
        record = _search_ledger(ctx, request, run_id, output)
        self.run_ledger.complete(ctx, run_id, record)
        self.session_ledger.record_tool_completed(
            ctx, request.application_id, request.session_id, request.tool_id, run_id
        )
        return _result_payload(run_id, record.id, output, is_replayed=False)

    def _activated_tool_ids(self, ctx: RequestContext, application_id: str, session_id: str) -> set[str]:
        return self.session_ledger.activated_tool_ids(ctx, application_id, session_id)

    def _authorized_application_bundle(self, ctx: RequestContext, application_id: str) -> JsonObject:
        self.access_session_validator.require_active(ctx, application_id)
        if ctx.application_id != application_id or not ctx.client_id:
            raise PermissionDenied("Builder MCP requires an OAuth application and active client")
        if not ctx.token_scopes:
            raise PermissionDenied("Builder MCP requires resource-scoped OAuth token scopes")
        bundle = self.osdk_application_service.get_application(application_id, ctx=ctx)
        application = bundle.get("application")
        clients = bundle.get("clients")
        if not isinstance(application, Mapping) or application.get("status") != "active":
            raise PermissionDenied("Builder MCP application is not active")
        if not _has_active_client(clients, ctx.client_id):
            raise PermissionDenied("Builder MCP OAuth client is not active for this application")
        return bundle

    def _mode_allowed(self, ctx: RequestContext, bundle: JsonObject, mode: str) -> bool:
        required = _mode_scope(mode)
        resources = bundle.get("resources")
        granted = (
            tuple(str(scope) for row in resources if isinstance(row, Mapping) for scope in row.get("scopes", ()))
            if isinstance(resources, list)
            else ()
        )
        return is_scope_allowed(required, ctx.token_scopes, granted)

    def _validated_request(
        self,
        request: FdeMcpToolCall,
        schema: Mapping[str, object],
    ) -> FdeMcpToolCall:
        arguments, receipt = _validated_outer_input(request, schema)
        return replace(request, arguments=arguments, confirmation_receipt=receipt)

    def _replay_or_confirm(
        self,
        ctx: RequestContext,
        request: FdeMcpToolCall,
        spec: ToolSpec,
        run_id: str,
        binding: FdeMcpRequestBinding,
    ) -> dict[str, object] | None:
        replay = self.security_ledger.replay(ctx, run_id, binding)
        if replay is not None:
            return _replay_result_payload(run_id, replay)
        if spec.effect == "READ":
            return None
        if request.confirmation_receipt is None:
            return self._confirmation_challenge(ctx, request, run_id, binding)
        return None

    def _confirmation_challenge(
        self,
        ctx: RequestContext,
        request: FdeMcpToolCall,
        run_id: str,
        binding: FdeMcpRequestBinding,
    ) -> dict[str, object]:
        challenge = self.security_ledger.issue_challenge(ctx, run_id, binding)
        structured = challenge.get("structuredContent")
        if challenge.get("isReplayed") is not True:
            self.session_ledger.append_event(
                ctx,
                request.application_id,
                request.session_id,
                "notifications/foundry-lite/approval_required",
                dict(structured) if isinstance(structured, Mapping) else {},
            )
        return challenge

    def _require_lazy_activation(
        self,
        ctx: RequestContext,
        request: FdeMcpToolCall,
        spec: ToolSpec,
    ) -> None:
        activated = self._activated_tool_ids(ctx, request.application_id, request.session_id)
        if LAZY_DISCOVERY_MARKER in activated and spec.tool_id not in activated:
            raise PermissionDenied(
                "Builder MCP tool must be activated by search_tools in a lazy-discovery session",
                details={"reason": "tool_not_activated", "toolId": spec.tool_id},
            )

    def _claim_run(
        self,
        ctx: RequestContext,
        request: FdeMcpToolCall,
        binding: FdeMcpRequestBinding,
        run_id: str,
        catalog: tuple[ToolSpec, ...],
    ) -> dict[str, object] | None:
        if self.run_ledger.seed(ctx, request, binding, run_id, catalog):
            return None
        replay = self.security_ledger.replay(ctx, run_id, binding)
        if replay is None:
            raise ValidationFailed("Builder MCP run claim disappeared")
        return _replay_result_payload(run_id, replay)
