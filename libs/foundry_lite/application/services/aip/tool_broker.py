"""AIP Tool Broker (P0e, §8.8/§9.5).

The broker treats model tool calls as untrusted requests. It validates the
agent allowlist, tool spec, JSON schema, user permission, object/property scope,
masked properties, model egress compatibility, budget, and confirmation policy
before any server-side executor is called.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from foundry_lite.application.ports.ai_run_repository import AiToolCallRecord
from foundry_lite.application.ports.tool_executor import ConfirmationPolicy, ToolEffect, ToolExecutionRequest
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied
from foundry_lite.security.policy import PolicyService

JsonObject = Mapping[str, object]
JsonTypeChecker = Callable[[object], bool]

_JSON_TYPE_CHECKS: dict[str, JsonTypeChecker] = {
    "string": lambda value: isinstance(value, str),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "number": lambda value: isinstance(value, int | float) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "object": lambda value: isinstance(value, Mapping),
    "array": lambda value: isinstance(value, list),
}


@dataclass(frozen=True)
class ToolSpec:
    """Published tool definition visible to an agent version."""

    tool_id: str
    version: str
    input_schema: JsonObject
    output_schema: JsonObject
    effect: ToolEffect
    required_permission: str
    confirmation_policy: ConfirmationPolicy
    object_type_allowlist: tuple[str, ...] = ()
    property_allowlist: tuple[str, ...] = ()
    timeout_seconds: int = 30
    max_result_items: int = 50
    result_classification: str = "public"
    status: str = "published"


@dataclass(frozen=True)
class ToolCallRequest:
    """One model-requested tool call."""

    tool_call_id: str
    tool_id: str
    version: str
    arguments_json: str
    ai_run_id: str
    sequence: int
    request_id: str
    idempotency_key: str = ""
    occurred_at: str = "1970-01-01T00:00:00Z"


@dataclass(frozen=True)
class ToolBrokerRequest:
    """Broker inputs for one bounded tool call."""

    tool_call: ToolCallRequest
    tool_manifest: tuple[ToolSpec, ...]
    agent_allowed_tools: tuple[str, ...]
    model_allowed_classifications: tuple[str, ...] = ("public",)
    remaining_timeout_seconds: int = 30
    max_tool_output_bytes: int = 4096


@dataclass(frozen=True)
class ToolBrokerResult:
    """Validated, masked tool output plus ledger-ready call record."""

    tool_call_id: str
    status: str
    authorization_decision: str
    output_json: JsonObject
    redacted_preview: str
    arguments_hash: str
    result_hash: str
    ledger_record: AiToolCallRecord


@dataclass
class ToolBrokerError(Exception):
    """Typed fail-closed tool broker rejection."""

    reason: str
    detail: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.detail)


class ToolBrokerService(CoreService):
    """Validate and execute server-side read-only tools as the invoking user."""

    required_dependencies = ("policy", "tool_executor")
    required_collaborators = ()

    def execute(self, ctx: RequestContext, request: ToolBrokerRequest) -> ToolBrokerResult:
        spec = _published_spec(request)
        arguments = _validated_arguments(spec, request.tool_call.arguments_json)
        self._authorize(ctx, request, spec, arguments)
        execution = self.tool_executor.execute(ctx, _execution_request(request, arguments))
        output = _bounded_output(ctx, request, spec, dict(execution.output_json), self.policy)
        return _broker_result(ctx, request, spec, output, arguments, execution.status)

    def _authorize(self, ctx: RequestContext, request: ToolBrokerRequest, spec: ToolSpec, args: JsonObject) -> None:
        _require_policy(ctx, self.policy, spec.required_permission)
        _check_object_scope(spec, args)
        _check_masked_properties(ctx, self.policy, args)
        _check_model_egress(request, spec)
        _check_timeout_budget(request, spec)
        _check_confirmation(spec)


def _published_spec(request: ToolBrokerRequest) -> ToolSpec:
    call = request.tool_call
    if call.tool_id not in request.agent_allowed_tools:
        raise ToolBrokerError("not_allowed", f"tool {call.tool_id} is not in the agent manifest")
    spec = _matching_spec(call.tool_id, call.version, request.tool_manifest)
    if spec is None or spec.status != "published":
        raise ToolBrokerError("not_published", f"tool {call.tool_id}@{call.version} is not published")
    return spec


def _matching_spec(tool_id: str, version: str, specs: Sequence[ToolSpec]) -> ToolSpec | None:
    for spec in specs:
        if spec.tool_id == tool_id and spec.version == version:
            return spec
    return None


def _validated_arguments(spec: ToolSpec, arguments_json: str) -> dict[str, object]:
    try:
        value = json.loads(arguments_json)
    except json.JSONDecodeError as exc:
        raise ToolBrokerError("schema_invalid", "tool arguments must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ToolBrokerError("schema_invalid", "tool arguments must be a JSON object")
    _validate_json_object(spec.input_schema, value)
    return value


def _validate_json_object(schema: JsonObject, value: JsonObject) -> None:
    if schema.get("type") not in (None, "object"):
        raise ToolBrokerError("schema_invalid", "tool input schema must describe an object")
    _validate_required(schema, value)
    _validate_properties(schema, value)


def _validate_required(schema: JsonObject, value: JsonObject) -> None:
    required = schema.get("required", ())
    if not isinstance(required, list | tuple):
        raise ToolBrokerError("schema_invalid", "schema required must be a list")
    for key in required:
        if isinstance(key, str) and key not in value:
            raise ToolBrokerError("schema_invalid", f"missing required argument {key}")


def _validate_properties(schema: JsonObject, value: JsonObject) -> None:
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise ToolBrokerError("schema_invalid", "schema properties must be an object")
    if schema.get("additionalProperties") is False:
        _reject_extra_keys(value, properties)
    for key, property_schema in properties.items():
        if key in value and isinstance(key, str) and isinstance(property_schema, Mapping):
            _validate_value_type(key, value[key], property_schema.get("type"))


def _reject_extra_keys(value: JsonObject, properties: Mapping[object, object]) -> None:
    allowed = {key for key in properties if isinstance(key, str)}
    extra = sorted(set(value) - allowed)
    if extra:
        raise ToolBrokerError("schema_invalid", f"unexpected argument {extra[0]}")


def _validate_value_type(key: str, value: object, expected: object) -> None:
    if expected is None or _matches_json_type(value, expected):
        return
    raise ToolBrokerError("schema_invalid", f"argument {key} does not match type {expected}")


def _matches_json_type(value: object, expected: object) -> bool:
    checker = _JSON_TYPE_CHECKS.get(expected) if isinstance(expected, str) else None
    return checker(value) if checker else False


def _require_policy(ctx: RequestContext, policy: PolicyService, permission: str) -> None:
    try:
        policy.require(ctx, permission)
    except PermissionDenied as exc:
        raise ToolBrokerError("policy_denied", f"permission denied for {permission}") from exc


def _check_object_scope(spec: ToolSpec, args: JsonObject) -> None:
    object_type = _object_type(args)
    if spec.object_type_allowlist and object_type not in spec.object_type_allowlist:
        raise ToolBrokerError("object_scope_denied", f"object type {object_type} is not allowlisted")
    requested = _requested_properties(args)
    denied = requested - set(spec.property_allowlist) if spec.property_allowlist else set()
    if denied:
        raise ToolBrokerError("property_scope_denied", f"property {sorted(denied)[0]} is not allowlisted")


def _check_masked_properties(ctx: RequestContext, policy: PolicyService, args: JsonObject) -> None:
    object_type = _object_type(args)
    if object_type is None:
        return
    denied = _requested_properties(args) & policy.masked_property_names(ctx, object_type)
    if denied:
        raise ToolBrokerError("masked_property_denied", f"property {sorted(denied)[0]} is masked for caller")


def _check_model_egress(request: ToolBrokerRequest, spec: ToolSpec) -> None:
    if spec.result_classification not in request.model_allowed_classifications:
        raise ToolBrokerError("egress_incompatible", "tool result classification is not allowed by the model")


def _check_timeout_budget(request: ToolBrokerRequest, spec: ToolSpec) -> None:
    if spec.timeout_seconds > request.remaining_timeout_seconds:
        raise ToolBrokerError("budget_exceeded", "tool timeout exceeds remaining run budget")


def _check_confirmation(spec: ToolSpec) -> None:
    if spec.effect != "READ" or spec.confirmation_policy != "NONE":
        raise ToolBrokerError("confirmation_required", "non-read tool requires confirmation or review")


def _execution_request(request: ToolBrokerRequest, args: JsonObject) -> ToolExecutionRequest:
    return ToolExecutionRequest(
        tool_id=request.tool_call.tool_id,
        version=request.tool_call.version,
        input_json=args,
        ai_run_id=request.tool_call.ai_run_id,
        request_id=request.tool_call.request_id,
        idempotency_key=request.tool_call.idempotency_key or _hash_json(args),
    )


def _bounded_output(
    ctx: RequestContext, request: ToolBrokerRequest, spec: ToolSpec, output: dict[str, object], policy: PolicyService
) -> dict[str, object]:
    _check_result_items(spec, output)
    masked = _mask_output(ctx, policy, output)
    _check_output_bytes(request, masked)
    return masked


def _check_result_items(spec: ToolSpec, output: JsonObject) -> None:
    items = output.get("items")
    if isinstance(items, list) and len(items) > spec.max_result_items:
        raise ToolBrokerError("budget_exceeded", "tool result exceeds max_result_items")


def _check_output_bytes(request: ToolBrokerRequest, output: JsonObject) -> None:
    if len(_json_block(output).encode()) > request.max_tool_output_bytes:
        raise ToolBrokerError("budget_exceeded", "tool result exceeds max_tool_output_bytes")


def _mask_output(ctx: RequestContext, policy: PolicyService, output: dict[str, object]) -> dict[str, object]:
    output = _mask_top_level_properties(ctx, policy, output)
    items = output.get("items")
    if isinstance(items, list):
        output["items"] = [_mask_item(ctx, policy, item) for item in items]
    return output


def _mask_top_level_properties(
    ctx: RequestContext, policy: PolicyService, output: dict[str, object]
) -> dict[str, object]:
    object_type = _object_type(output)
    properties = output.get("properties")
    if object_type is None or not isinstance(properties, Mapping):
        return dict(output)
    masked = dict(output)
    masked["properties"] = policy.mask_properties(ctx, object_type, dict(properties))
    return masked


def _mask_item(ctx: RequestContext, policy: PolicyService, item: object) -> object:
    if not isinstance(item, Mapping):
        return item
    return _mask_top_level_properties(ctx, policy, dict(item))


def _broker_result(
    ctx: RequestContext,
    request: ToolBrokerRequest,
    spec: ToolSpec,
    output: JsonObject,
    arguments: JsonObject,
    status: str,
) -> ToolBrokerResult:
    arguments_hash = _hash_json(arguments)
    result_hash = _hash_json(output)
    ledger = _ledger_record(ctx, request, spec, arguments_hash, result_hash, status)
    return ToolBrokerResult(
        tool_call_id=request.tool_call.tool_call_id,
        status=status,
        authorization_decision="allowed",
        output_json=output,
        redacted_preview=_redacted_preview(output),
        arguments_hash=arguments_hash,
        result_hash=result_hash,
        ledger_record=ledger,
    )


def _ledger_record(
    ctx: RequestContext, request: ToolBrokerRequest, spec: ToolSpec, arguments_hash: str, result_hash: str, status: str
) -> AiToolCallRecord:
    return AiToolCallRecord(
        id=request.tool_call.tool_call_id,
        tenant_id=ctx.tenant_id,
        ai_run_id=request.tool_call.ai_run_id,
        sequence=request.tool_call.sequence,
        tool_id=spec.tool_id,
        tool_version=spec.version,
        arguments_hash=arguments_hash,
        effect=spec.effect,
        authorization_decision="allowed",
        confirmation_policy=spec.confirmation_policy,
        status=status,
        result_hash=result_hash,
        linked_action_run_id=None,
        started_at=request.tool_call.occurred_at,
        completed_at=request.tool_call.occurred_at,
        error_json=None,
    )


def _object_type(value: JsonObject) -> str | None:
    for key in ("object_type", "objectType", "target_object_type", "targetObjectType"):
        if isinstance(value.get(key), str):
            return str(value[key])
    return None


def _requested_properties(args: JsonObject) -> set[str]:
    names = args.get("property_names", args.get("propertyNames", args.get("properties", ())))
    if isinstance(names, Mapping):
        return {key for key in names if isinstance(key, str)}
    if isinstance(names, list | tuple):
        return {name for name in names if isinstance(name, str)}
    return set()


def _redacted_preview(output: JsonObject) -> str:
    return _json_block(output)[:512]


def _hash_json(value: object) -> str:
    return f"sha256:{hashlib.sha256(_json_block(value).encode()).hexdigest()}"


def _json_block(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
