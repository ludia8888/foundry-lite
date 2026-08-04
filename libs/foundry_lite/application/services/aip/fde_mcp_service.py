"""Builder MCP catalog and direct governed tool execution with durable AI evidence."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.ports.ai_run_repository import (
    AiExecutionRunRecord,
    AiRunRepository,
    AiSessionRecord,
    AiToolCallRecord,
)
from foundry_lite.application.ports.transaction_context import AI_RUN_FAILED, AI_RUN_SUCCEEDED, TransactionManager
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.aip.agent_runtime_ledger import event_record, hash_json
from foundry_lite.application.services.aip.fde_catalog import FDE_MODES, fde_tool_catalog
from foundry_lite.application.services.aip.fde_ontology_tools import FdeOntologyToolError
from foundry_lite.application.services.aip.fde_tool_result import FdePlatformToolError, FdePlatformToolRequest
from foundry_lite.application.services.aip.tool_broker import ToolBrokerResult, ToolSpec
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied, ValidationFailed
from foundry_lite.domain.platform.scopes import is_scope_allowed, resource_scope
from foundry_lite.security.policy import PolicyService

JsonObject = Mapping[str, object]


@dataclass(frozen=True)
class FdeMcpToolCall:
    """Normalized one-tool JSON-RPC request at the Builder MCP boundary."""

    application_id: str
    session_id: str
    json_rpc_id: str
    mode: str
    workspace_ref: str
    tool_id: str
    arguments: JsonObject
    confirmed_tool_id: str | None


class FdeMcpContextValidator(Protocol):
    """Validate MCP workspace scope through the canonical FDE context layer."""

    def validate_scope(self, ctx: RequestContext, mode: str, workspace_ref: str) -> None: ...


class FdeMcpPlatformExecutor(Protocol):
    """Execute a server-owned tool through the governed FDE dispatcher."""

    def execute(self, ctx: RequestContext, request: FdePlatformToolRequest) -> ToolBrokerResult: ...


class FdeMcpApplicationReader(Protocol):
    """Read the OAuth application's active clients and resource restrictions."""

    def get_application(self, app_id: str, *, ctx: RequestContext | None = None) -> JsonObject: ...


class FdeMcpGateway:
    """External MCP integration boundary over governed application services."""

    def __init__(
        self,
        *,
        engine: TransactionManager,
        policy: PolicyService,
        ai_run_repository: AiRunRepository,
        context_validator: FdeMcpContextValidator,
        platform_executor: FdeMcpPlatformExecutor,
        application_reader: FdeMcpApplicationReader,
    ) -> None:
        self.engine = engine
        self.policy = policy
        self.ai_run_repository = ai_run_repository
        self.fde_context_service = context_validator
        self.fde_platform_tool_service = platform_executor
        self.osdk_application_service = application_reader

    def list_tools(self, ctx: RequestContext, application_id: str) -> dict[str, object]:
        bundle = self._authorized_application_bundle(ctx, application_id)
        allowed: dict[str, tuple[ToolSpec, set[str]]] = {}
        for mode in FDE_MODES:
            if self._mode_allowed(ctx, bundle, mode.mode_id):
                for tool in fde_tool_catalog(mode.mode_id, ()):
                    if self.policy.decide(ctx, tool.required_permission).allowed:
                        allowed.setdefault(tool.tool_id, (tool, set()))[1].add(mode.mode_id)
        return {"tools": [_mcp_tool(tuple(sorted(modes)), tool) for tool_id, (tool, modes) in sorted(allowed.items())]}

    def execute_tool(self, ctx: RequestContext, request: FdeMcpToolCall) -> dict[str, object]:
        bundle = self._authorized_application_bundle(ctx, request.application_id)
        if not self._mode_allowed(ctx, bundle, request.mode):
            raise PermissionDenied("Builder MCP mode is outside application restrictions")
        self.fde_context_service.validate_scope(ctx, request.mode, request.workspace_ref)
        catalog = fde_tool_catalog(request.mode, ())
        spec = _tool_spec(catalog, request.tool_id)
        _guard_external_tool(spec)
        run_id = _mcp_run_id(ctx, request)
        replay = self._replay(ctx, run_id)
        if replay is not None:
            return replay
        self._seed_run(ctx, request, run_id, catalog)
        try:
            result = self.fde_platform_tool_service.execute(
                ctx,
                _platform_request(request, run_id, spec, catalog),
            )
        except (FdePlatformToolError, FdeOntologyToolError) as exc:
            domain_error = _tool_domain_error(exc)
            self._fail_run(ctx, run_id, domain_error)
            raise domain_error from exc
        except Exception as exc:
            self._fail_run(ctx, run_id, exc)
            raise
        self._complete_run(ctx, run_id, result.ledger_record)
        return _result_payload(run_id, result.tool_call_id, result.output_json, is_replayed=False)

    def _authorized_application_bundle(self, ctx: RequestContext, application_id: str) -> JsonObject:
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

    def _replay(self, ctx: RequestContext, run_id: str) -> dict[str, object] | None:
        with self.engine.begin() as conn:
            ledger = self.ai_run_repository.ledger_for_run(transaction=conn, tenant_id=ctx.tenant_id, ai_run_id=run_id)
        if ledger is None:
            return None
        calls = ledger["toolCalls"]
        output = calls[0].get("result_json") if calls else None
        if not isinstance(output, Mapping):
            raise ValidationFailed("Builder MCP idempotent run exists without terminal tool evidence")
        return _result_payload(run_id, str(calls[0]["id"]), output, is_replayed=True)

    def _seed_run(
        self,
        ctx: RequestContext,
        request: FdeMcpToolCall,
        run_id: str,
        catalog: tuple[ToolSpec, ...],
    ) -> None:
        now = _now()
        with self.engine.begin() as conn:
            self.ai_run_repository.create_session(
                transaction=conn,
                record=AiSessionRecord(
                    id=request.session_id,
                    tenant_id=ctx.tenant_id,
                    agent_version_id=f"builder-mcp:{request.application_id}:v1",
                    actor_user_id=ctx.actor_user_id,
                    status="active",
                    created_at=now,
                    last_activity_at=now,
                ),
            )
            self.ai_run_repository.create_execution_run(
                transaction=conn,
                record=_run_record(ctx, request, run_id, catalog, now),
            )
            self.ai_run_repository.append_execution_event(
                transaction=conn,
                record=event_record(ctx, run_id, 1, "mcp_tool_running", {"toolId": request.tool_id}, now),
            )

    def _complete_run(self, ctx: RequestContext, run_id: str, tool_record: AiToolCallRecord) -> None:
        now = _now()
        with self.engine.begin() as conn:
            self.ai_run_repository.record_tool_call(transaction=conn, record=tool_record)
            self.ai_run_repository.append_execution_event(
                transaction=conn,
                record=event_record(ctx, run_id, 2, "succeeded", {"source": "builder_mcp"}, now),
            )
            self.ai_run_repository.update_execution_run_status(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                ai_run_id=run_id,
                transition=AI_RUN_SUCCEEDED,
                usage_json={"modelCallCount": 0, "toolCallCount": 1, "source": "builder_mcp"},
                error_json=None,
                completed_at=now,
            )

    def _fail_run(self, ctx: RequestContext, run_id: str, exc: Exception) -> None:
        now = _now()
        error = {"type": type(exc).__name__, "detail": str(exc)[:512]}
        with self.engine.begin() as conn:
            self.ai_run_repository.append_execution_event(
                transaction=conn,
                record=event_record(ctx, run_id, 2, "failed", error, now),
            )
            self.ai_run_repository.update_execution_run_status(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                ai_run_id=run_id,
                transition=AI_RUN_FAILED,
                usage_json={"modelCallCount": 0, "toolCallCount": 0, "source": "builder_mcp"},
                error_json=error,
                completed_at=now,
            )


def _run_record(
    ctx: RequestContext,
    request: FdeMcpToolCall,
    run_id: str,
    catalog: tuple[ToolSpec, ...],
    now: str,
) -> AiExecutionRunRecord:
    """Build the immutable durable MCP execution-run evidence."""
    request_hash = hash_json({"mode": request.mode, "scope": request.workspace_ref, "arguments": request.arguments})
    return AiExecutionRunRecord(
        id=run_id,
        tenant_id=ctx.tenant_id,
        session_id=request.session_id,
        agent_version_id=f"builder-mcp:{request.application_id}:v1",
        actor_user_id=ctx.actor_user_id,
        request_id=ctx.request_id,
        trace_id=ctx.request_id,
        status="running",
        ontology_version_id="active-ontology",
        model_alias_version="none",
        resolved_model_id="none",
        resolved_model_revision="none",
        prompt_version_id="builder-mcp-direct-v1",
        compiled_prompt_hash=request_hash,
        tool_manifest_hash=hash_json([tool.tool_id for tool in catalog]),
        context_manifest_hash=hash_json([request.workspace_ref]),
        state_snapshot_hash=request_hash,
        policy_snapshot_hash=hash_json({"policy": "builder-mcp-v1"}),
        budget_json={"maxToolCalls": 1, "maxModelCalls": 0},
        usage_json=None,
        error_json=None,
        started_at=now,
        completed_at=None,
    )


def _platform_request(
    request: FdeMcpToolCall,
    run_id: str,
    spec: ToolSpec,
    catalog: tuple[ToolSpec, ...],
) -> FdePlatformToolRequest:
    """Translate a confirmed MCP call into the internal governed tool contract."""
    approved = (spec.tool_id,) if request.confirmed_tool_id == spec.tool_id else ()
    return FdePlatformToolRequest(
        tool_call_id=f"{run_id}-tool-1",
        ai_run_id=run_id,
        sequence=1,
        mode=request.mode,
        scope_ref=request.workspace_ref,
        spec=spec,
        catalog=catalog,
        arguments=request.arguments,
        approved_tool_ids=approved,
        max_output_bytes=65536,
        occurred_at=_now(),
    )


def _mcp_tool(modes: tuple[str, ...], tool: ToolSpec) -> dict[str, object]:
    """Project one deterministic MCP tool schema."""
    return {
        "name": tool.tool_id,
        "title": tool.tool_id,
        "description": tool.description,
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": list(modes)},
                "workspaceRef": {"type": "string"},
                "arguments": dict(tool.input_schema),
            },
            "required": ["mode", "workspaceRef", "arguments"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": tool.effect == "READ",
            "destructiveHint": tool.effect == "WRITE",
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }


def _mode_scope(mode: str) -> str:
    """Return the resource restriction required to execute one FDE mode."""
    return resource_scope("connector", f"fde_{mode}", "execute")


def _has_active_client(value: object, client_id: str) -> bool:
    """Check that the calling OAuth client is active for the application."""
    return isinstance(value, list) and any(
        isinstance(item, Mapping) and item.get("client_id") == client_id and item.get("status") == "active"
        for item in value
    )


def _tool_spec(catalog: tuple[ToolSpec, ...], tool_id: str) -> ToolSpec:
    """Resolve a requested tool from the selected mode's server catalog."""
    for tool in catalog:
        if tool.tool_id == tool_id:
            return tool
    raise ValidationFailed("Builder MCP tool is not available in the selected mode")


def _guard_external_tool(spec: ToolSpec) -> None:
    """Keep approval, merge, deployment, and activation outside MCP."""
    if spec.tool_id.endswith((".execute_proposal", ".approve", ".merge", ".deploy", ".activate")):
        raise PermissionDenied("Builder MCP never exposes approval, merge, deploy, or activation tools")


def _tool_domain_error(exc: FdePlatformToolError | FdeOntologyToolError) -> PermissionDenied | ValidationFailed:
    """Map internal FDE tool failures to stable API-domain errors."""
    if exc.reason in {"approval_required", "tool_approval_required"}:
        return PermissionDenied(exc.detail, details={"reason": exc.reason})
    return ValidationFailed(exc.detail, details={"reason": exc.reason})


def _mcp_run_id(ctx: RequestContext, request: FdeMcpToolCall) -> str:
    """Derive a replay-safe run id from tenant, app, session, and JSON-RPC id."""
    raw = ":".join((ctx.tenant_id, request.application_id, request.session_id, request.json_rpc_id, request.tool_id))
    return f"aip-mcp-{hashlib.sha256(raw.encode()).hexdigest()[:32]}"


def _result_payload(
    run_id: str,
    tool_call_id: str,
    output: Mapping[str, object],
    *,
    is_replayed: bool,
) -> dict[str, object]:
    """Return native structured MCP content and durable run coordinates."""
    return {
        "aiRunId": run_id,
        "toolCallId": tool_call_id,
        "structuredContent": dict(output),
        "content": [{"type": "text", "text": "Governed Builder MCP tool completed."}],
        "isError": False,
        "isReplayed": is_replayed,
    }
