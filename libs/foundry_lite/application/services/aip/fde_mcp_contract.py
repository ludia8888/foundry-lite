"""Pure Builder MCP request, schema, run, and result contracts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.ports import AiExecutionRunRecord
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.aip.agent_runtime_ledger import hash_json
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import (
    FdeMcpReplay,
    FdeMcpRequestBinding,
    execution_binding_budget,
    request_binding,
)
from foundry_lite.application.services.aip.fde_ontology_tools import FdeOntologyToolError
from foundry_lite.application.services.aip.fde_tool_result import FdePlatformToolError, FdePlatformToolRequest
from foundry_lite.application.services.aip.tool_broker import ToolSpec
from foundry_lite.application.services.mcp_json_rpc import JsonRpcRequestId, internal_mcp_request_id
from foundry_lite.application.services.mcp_json_schema import McpJsonSchemaError, validate_mcp_json_schema
from foundry_lite.application.services.mcp_tool_results import serialized_text_content, tool_error_result
from foundry_lite.application.services.runtime_error_payloads import scrub_error_text
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, PermissionDenied, ValidationFailed
from foundry_lite.domain.platform.scopes import is_scope_allowed as is_scope_allowed
from foundry_lite.domain.platform.scopes import resource_scope

JsonObject = Mapping[str, object]


class FdeMcpCallLike(Protocol):
    @property
    def application_id(self) -> str: ...

    @property
    def session_id(self) -> str: ...

    @property
    def json_rpc_id(self) -> JsonRpcRequestId: ...

    @property
    def mode(self) -> str: ...

    @property
    def workspace_ref(self) -> str: ...

    @property
    def tool_id(self) -> str: ...

    @property
    def arguments(self) -> JsonObject: ...

    @property
    def confirmation_receipt(self) -> str | None: ...

    @property
    def raw_input(self) -> JsonObject | None: ...

    @property
    def origin(self) -> str | None: ...


def validated_outer_input(
    request: FdeMcpCallLike,
    schema: Mapping[str, object],
) -> tuple[dict[str, object], str | None]:
    raw = dict(request.raw_input) if request.raw_input is not None else _normalized_outer_input(request)
    try:
        validate_mcp_json_schema(raw, schema)
    except McpJsonSchemaError as exc:
        raise ValidationFailed(
            "Builder MCP tools/call input does not match the advertised inputSchema",
            details={"path": exc.path, "reason": exc.reason},
        ) from exc
    _require_normalized_fields_match(request, raw)
    arguments = raw.get("arguments")
    receipt = raw.get("confirmationReceipt")
    return dict(arguments) if isinstance(arguments, Mapping) else {}, receipt if isinstance(receipt, str) else None


def validate_outer_shape(request: FdeMcpCallLike, schema: Mapping[str, object]) -> None:
    """Reject outer envelope drift before replay without inspecting nested tool values."""
    raw = dict(request.raw_input) if request.raw_input is not None else _normalized_outer_input(request)
    properties = schema.get("properties")
    projected = dict(properties) if isinstance(properties, Mapping) else {}
    projected["arguments"] = {"type": "object"}
    shape = {**schema, "properties": projected}
    try:
        validate_mcp_json_schema(raw, shape)
    except McpJsonSchemaError as exc:
        raise ValidationFailed(
            "Builder MCP tools/call outer input does not match the advertised inputSchema",
            details={"path": exc.path, "reason": exc.reason},
        ) from exc
    _require_normalized_fields_match(request, raw)


def call_binding(ctx: RequestContext, request: FdeMcpCallLike, spec: ToolSpec) -> FdeMcpRequestBinding:
    return request_binding(
        ctx,
        application_id=request.application_id,
        session_id=request.session_id,
        tool_id=request.tool_id,
        mode=request.mode,
        workspace_ref=request.workspace_ref,
        arguments=request.arguments,
        required_permission=spec.required_permission,
        origin=request.origin,
    )


def tool_input_schema(modes: tuple[str, ...], tool: ToolSpec) -> dict[str, object]:
    properties: dict[str, object] = {
        "mode": {"type": "string", "enum": list(modes)},
        "workspaceRef": {"type": "string"},
        "arguments": dict(tool.input_schema),
    }
    if tool.effect != "READ":
        properties["confirmationReceipt"] = {"type": "string"}
    return {
        "type": "object",
        "properties": properties,
        "required": ["mode", "workspaceRef", "arguments"],
        "additionalProperties": False,
    }


def mcp_tool(modes: tuple[str, ...], tool: ToolSpec) -> dict[str, object]:
    return {
        "name": tool.tool_id,
        "title": tool.tool_id,
        "description": tool.description,
        "inputSchema": tool_input_schema(modes, tool),
        "annotations": {
            "readOnlyHint": tool.effect == "READ",
            "destructiveHint": tool.effect == "WRITE",
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }


def run_record(
    ctx: RequestContext,
    request: FdeMcpCallLike,
    binding: FdeMcpRequestBinding,
    run_id: str,
    catalog: tuple[ToolSpec, ...],
    now: str,
) -> AiExecutionRunRecord:
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
        prompt_version_id="builder-mcp-direct-v2",
        compiled_prompt_hash=binding.fingerprint,
        tool_manifest_hash=hash_json([tool.tool_id for tool in catalog]),
        context_manifest_hash=hash_json([request.workspace_ref]),
        state_snapshot_hash=binding.fingerprint,
        policy_snapshot_hash=hash_json({"policy": "builder-mcp-v2"}),
        budget_json=execution_binding_budget(binding),
        usage_json=None,
        error_json=None,
        started_at=now,
        completed_at=None,
    )


def platform_request(
    request: FdeMcpCallLike,
    run_id: str,
    spec: ToolSpec,
    catalog: tuple[ToolSpec, ...],
    *,
    is_confirmed: bool,
) -> FdePlatformToolRequest:
    return FdePlatformToolRequest(
        tool_call_id=f"{run_id}-tool-1",
        ai_run_id=run_id,
        sequence=1,
        mode=request.mode,
        scope_ref=request.workspace_ref,
        spec=spec,
        catalog=catalog,
        arguments=request.arguments,
        approved_tool_ids=(spec.tool_id,) if is_confirmed else (),
        max_output_bytes=65536,
        occurred_at=_now(),
    )


def mode_scope(mode: str) -> str:
    return resource_scope("connector", f"fde_{mode}", "execute")


def has_active_client(value: object, client_id: str) -> bool:
    return isinstance(value, list) and any(
        isinstance(item, Mapping) and item.get("client_id") == client_id and item.get("status") == "active"
        for item in value
    )


def tool_spec(catalog: tuple[ToolSpec, ...], tool_id: str) -> ToolSpec:
    for tool in catalog:
        if tool.tool_id == tool_id:
            return tool
    raise ValidationFailed("Builder MCP tool is not available in the selected mode")


def guard_external_tool(spec: ToolSpec) -> None:
    if spec.tool_id.endswith((".execute_proposal", ".approve", ".merge", ".deploy", ".activate")):
        raise PermissionDenied("Builder MCP never exposes approval, merge, deploy, or activation tools")


def tool_domain_error(exc: FdePlatformToolError | FdeOntologyToolError) -> PermissionDenied | ValidationFailed:
    if exc.reason in {"approval_required", "tool_approval_required"}:
        return PermissionDenied(scrub_error_text(exc.detail), details={"reason": scrub_error_text(exc.reason)})
    return ValidationFailed(scrub_error_text(exc.detail), details={"reason": scrub_error_text(exc.reason)})


def mcp_run_id(
    ctx: RequestContext,
    request: FdeMcpCallLike,
    write_binding: FdeMcpRequestBinding | None = None,
) -> str:
    if write_binding is not None:
        raw = ":".join((ctx.tenant_id, request.application_id, write_binding.execution_fingerprint))
    else:
        identity = internal_mcp_request_id(request.json_rpc_id)
        raw = ":".join((ctx.tenant_id, request.application_id, request.session_id, identity))
    return f"aip-mcp-{hashlib.sha256(raw.encode()).hexdigest()[:32]}"


def result_payload(
    run_id: str,
    tool_call_id: str,
    output: Mapping[str, object],
    *,
    is_replayed: bool,
    is_error: bool = False,
) -> dict[str, object]:
    return {
        "aiRunId": run_id,
        "toolCallId": tool_call_id,
        "structuredContent": dict(output),
        "content": serialized_text_content(output),
        "isError": is_error,
        "isReplayed": is_replayed,
    }


def replay_result_payload(run_id: str, replay: FdeMcpReplay) -> dict[str, object]:
    return result_payload(
        run_id,
        replay.tool_call_id,
        replay.output,
        is_replayed=True,
        is_error=replay.is_error,
    )


def error_result_payload(
    run_id: str,
    tool_call_id: str,
    exc: FoundryLiteError,
    *,
    request_id: str,
) -> dict[str, object]:
    return {
        "aiRunId": run_id,
        "toolCallId": tool_call_id,
        **tool_error_result(exc, request_id=request_id),
        "isReplayed": False,
    }


def _normalized_outer_input(request: FdeMcpCallLike) -> dict[str, object]:
    value: dict[str, object] = {
        "mode": request.mode,
        "workspaceRef": request.workspace_ref,
        "arguments": dict(request.arguments),
    }
    if request.confirmation_receipt is not None:
        value["confirmationReceipt"] = request.confirmation_receipt
    return value


def _require_normalized_fields_match(request: FdeMcpCallLike, raw: Mapping[str, object]) -> None:
    expected = (request.mode, request.workspace_ref, dict(request.arguments))
    observed = (raw.get("mode"), raw.get("workspaceRef"), raw.get("arguments"))
    if observed != expected:
        raise ValidationFailed(
            "Builder MCP normalized call does not match its raw tools/call input",
            details={"reason": "normalized_input_mismatch"},
        )
    receipt = raw.get("confirmationReceipt")
    if request.confirmation_receipt is not None and receipt != request.confirmation_receipt:
        raise ValidationFailed(
            "Builder MCP confirmation receipt does not match its raw tools/call input",
            details={"reason": "confirmation_receipt_mismatch"},
        )
