"""Shared execution and durable-result helpers for AI FDE platform tools."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.ports.ai_run_repository import AiToolCallRecord
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.aip.tool_broker import ToolBrokerResult, ToolSpec
from foundry_lite.domain.context import RequestContext

JsonObject = Mapping[str, object]


@dataclass(frozen=True)
class FdePlatformToolRequest:
    tool_call_id: str
    ai_run_id: str
    sequence: int
    mode: str
    scope_ref: str
    spec: ToolSpec
    catalog: tuple[ToolSpec, ...]
    arguments: JsonObject
    approved_tool_ids: tuple[str, ...]
    max_output_bytes: int
    occurred_at: str


@dataclass
class FdePlatformToolError(Exception):
    reason: str
    detail: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.detail)


def require_tool_approval(request: FdePlatformToolRequest) -> None:
    if request.spec.effect == "READ":
        return
    if request.spec.tool_id not in request.approved_tool_ids:
        raise FdePlatformToolError(
            "tool_approval_required",
            f"explicit user approval is required for {request.spec.tool_id} on {request.scope_ref}",
        )


def platform_tool_result(
    ctx: RequestContext,
    request: FdePlatformToolRequest,
    output: JsonObject,
) -> ToolBrokerResult:
    public_output = _bounded_output(request, output)
    arguments_hash = hash_json(request.arguments)
    result_hash = hash_json(output)
    decision = "allowed_read" if request.spec.effect == "READ" else "allowed_by_user_preapproval"
    return ToolBrokerResult(
        tool_call_id=request.tool_call_id,
        status="succeeded",
        authorization_decision=decision,
        output_json=public_output,
        redacted_preview=json.dumps(public_output, sort_keys=True)[:512],
        arguments_hash=arguments_hash,
        result_hash=result_hash,
        ledger_record=_ledger(ctx, request, output, arguments_hash, result_hash, decision),
    )


def hash_json(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    # codeql[py/weak-sensitive-data-hashing]
    # This is a deterministic integrity fingerprint for arbitrary JSON ledger evidence, not a
    # password verifier or credential store. It must remain reproducible across processes so
    # later readback can prove that a governed result was not changed.
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


def required_text(value: JsonObject, key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise FdePlatformToolError("schema_invalid", f"{key} is required")
    return item.strip()


def required_integer(value: object, field: str) -> int:
    """Return one strict integer or raise the shared AI FDE schema error."""

    if not isinstance(value, int) or isinstance(value, bool):
        raise FdePlatformToolError("schema_invalid", f"{field} must be an integer")
    return value


def scope_value(scope_ref: str, prefix: str) -> str:
    if not scope_ref.startswith(prefix):
        raise FdePlatformToolError("scope_mismatch", f"tool requires a {prefix} scope")
    value = scope_ref.removeprefix(prefix).strip()
    if not value:
        raise FdePlatformToolError("scope_mismatch", f"{prefix} scope value is required")
    return value


def _bounded_output(request: FdePlatformToolRequest, output: JsonObject) -> JsonObject:
    payload = json.dumps(output, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(payload.encode()) <= request.max_output_bytes:
        return output
    if request.spec.effect == "READ":
        raise FdePlatformToolError("budget_exceeded", "AI FDE tool result exceeds max_tool_output_bytes")
    compact = {
        "isOutputTruncated": True,
        "resultHash": hash_json(output),
        "toolId": request.spec.tool_id,
        "nextStep": "Inspect the governed target to observe the completed mutation.",
    }
    if len(json.dumps(compact, sort_keys=True).encode()) > request.max_output_bytes:
        raise FdePlatformToolError("budget_exceeded", "AI FDE compact result exceeds max_tool_output_bytes")
    return compact


def _ledger(
    ctx: RequestContext,
    request: FdePlatformToolRequest,
    output: JsonObject,
    arguments_hash: str,
    result_hash: str,
    decision: str,
) -> AiToolCallRecord:
    return AiToolCallRecord(
        id=request.tool_call_id,
        tenant_id=ctx.tenant_id,
        ai_run_id=request.ai_run_id,
        sequence=request.sequence,
        tool_id=request.spec.tool_id,
        tool_version=request.spec.version,
        arguments_hash=arguments_hash,
        effect=request.spec.effect,
        authorization_decision=decision,
        confirmation_policy=request.spec.confirmation_policy,
        status="succeeded",
        result_hash=result_hash,
        linked_action_run_id=None,
        started_at=request.occurred_at,
        completed_at=_now(),
        error_json=None,
        result_json=dict(output),
    )
