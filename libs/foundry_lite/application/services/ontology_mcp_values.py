"""Pure validation and projection helpers for consumer Ontology MCP calls."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import TypedDict

from foundry_lite.application.action_types import ActionCatalogItem, ActionExecutionPlanResponse
from foundry_lite.application.services.mcp_json_rpc import JsonRpcRequestId, internal_mcp_request_id
from foundry_lite.application.services.mcp_tool_results import serialized_text_content
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.platform.scopes import is_scope_allowed

JsonObject = Mapping[str, object]


class ActionRequest(TypedDict):
    object_type: str
    object_id: str
    expected_object_version: int
    params: Mapping[str, object]


def action_description(item: ActionCatalogItem) -> str:
    description = item["contract"].get("agentToolDescription")
    if isinstance(description, str) and description.strip():
        return description.strip()
    return item["description"] or item["displayName"]


def can_autonomous_apply(item: ActionCatalogItem, plan: ActionExecutionPlanResponse) -> bool:
    return item["agentExecutionPolicy"] == "autonomous" and plan["risk"].get("effectiveLevel") == "low"


def grant_visible(ctx: RequestContext, grant: Mapping[str, object]) -> bool:
    return bool(effective_grant_scopes(ctx, grant))


def effective_grant_scopes(ctx: RequestContext, grant: Mapping[str, object]) -> tuple[str, ...]:
    """Return only scopes present in both the token and the durable application grant."""

    scopes = grant_scopes(grant)
    return tuple(scope for scope in scopes if is_scope_allowed(scope, ctx.token_scopes, scopes))


def grant_sort_key(grant: Mapping[str, object]) -> tuple[str, str]:
    return (grant_type(grant), grant_name(grant))


def grant_type(grant: Mapping[str, object]) -> str:
    value = grant.get("resource_type", grant.get("resourceType"))
    return value if isinstance(value, str) else ""


def grant_name(grant: Mapping[str, object]) -> str:
    value = grant.get("resource_api_name", grant.get("resourceApiName"))
    return value if isinstance(value, str) else ""


def grant_scopes(grant: Mapping[str, object]) -> tuple[str, ...]:
    value = grant.get("scopes")
    return tuple(str(item) for item in value) if isinstance(value, Sequence) and not isinstance(value, str) else ()


def parse_tool_name(name: str) -> tuple[str, str, str]:
    if name in {"action_run.get", "action_approval.get", "business_system.get"}:
        kind, operation = name.split(".")
        return (kind, "", operation)
    parts = name.split(".")
    if len(parts) != 3 or parts[0] not in {"object", "action", "function"}:
        raise ValidationFailed("Ontology MCP tool is not available")
    return (parts[0], parts[1], parts[2])


def mcp_idempotency_key(ctx: RequestContext, call: object) -> str:
    request_identity = internal_mcp_request_id(request_id_attr(call, "json_rpc_id"))
    values = (
        ctx.tenant_id,
        ctx.actor_user_id,
        text_attr(call, "application_id"),
        text_attr(call, "session_id"),
        request_identity,
        text_attr(call, "tool_name"),
    )
    return f"ontology-mcp-{hashlib.sha256(':'.join(values).encode()).hexdigest()}"


def action_request(arguments: JsonObject) -> ActionRequest:
    return {
        "object_type": text(arguments, "objectType"),
        "object_id": text(arguments, "objectId"),
        "expected_object_version": required_int(arguments, "expectedObjectVersion"),
        "params": mapping(arguments.get("params"), "params"),
    }


def mcp_result(result: Mapping[str, object]) -> dict[str, object]:
    return {
        "structuredContent": dict(result),
        "content": serialized_text_content(result),
        "isError": False,
    }


def tool_event_payload(call: object, result: Mapping[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {
        "jsonRpcId": request_id_attr(call, "json_rpc_id"),
        "toolName": text_attr(call, "tool_name"),
    }
    for source_key, target_key in (
        ("status", "resultStatus"),
        ("actionRunId", "actionRunId"),
        ("proposalId", "proposalId"),
        ("reviewId", "reviewId"),
    ):
        value = result.get(source_key)
        if isinstance(value, str | int | float | bool):
            payload[target_key] = value
    return payload


def mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValidationFailed("Ontology MCP field must be an object", details={"field": field})
    return value


def optional_mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValidationFailed("Ontology MCP field is required", details={"field": key})
    return item.strip()


def text_list(value: Mapping[str, object], key: str) -> list[str]:
    item = value.get(key)
    if not isinstance(item, Sequence) or isinstance(item, str | bytes) or not item:
        raise ValidationFailed("Ontology MCP field must be a non-empty list", details={"field": key})
    if not all(isinstance(entry, str) and entry.strip() for entry in item):
        raise ValidationFailed("Ontology MCP list entries must be non-empty strings", details={"field": key})
    return [str(entry).strip() for entry in item]


def optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def required_int(value: Mapping[str, object], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool):
        raise ValidationFailed("Ontology MCP integer field is required", details={"field": key})
    return item


def bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    resolved = default if value is None else value
    if not isinstance(resolved, int) or isinstance(resolved, bool) or not minimum <= resolved <= maximum:
        raise ValidationFailed("Ontology MCP integer is outside its allowed range")
    return resolved


def text_attr(value: object, name: str) -> str:
    item = getattr(value, name, None)
    if not isinstance(item, str) or not item:
        raise ValidationFailed("Ontology MCP call identity is invalid")
    return item


def request_id_attr(value: object, name: str) -> JsonRpcRequestId:
    item = getattr(value, name, None)
    if type(item) is str:
        return item
    if type(item) is int:
        return item
    raise ValidationFailed("Ontology MCP request identity is invalid")
