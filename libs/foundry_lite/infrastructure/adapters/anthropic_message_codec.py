"""Anthropic Messages API request and response normalization."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping, Sequence

from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailure, AdapterFailureKind
from foundry_lite.application.ports.language_model import (
    ModelInvocationRoute,
    ModelMediaContentResolver,
    ModelMediaReference,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
)

_MAX_REQUEST_BYTES = 32 * 1024 * 1024
_MAX_IMAGE_BASE64_BYTES = 10 * 1024 * 1024
_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})


def build_anthropic_payload(
    request: ModelRequest,
    media_resolver: ModelMediaContentResolver | None,
) -> dict[str, object]:
    """Compile one governed request into the current Anthropic Messages API body."""

    route = _required_route(request)
    if request.tools:
        raise _validation(
            "Anthropic tool definitions require typed schemas, not name-only tools",
            "tool_schema_required",
        )
    messages = _compiled_messages(request, route, media_resolver)
    if not messages or not any(message["role"] == "user" for message in messages):
        raise _validation("Anthropic Messages API requires at least one user message", "user_message_required")
    payload: dict[str, object] = {
        "model": route.provider_model_id,
        "max_tokens": request.max_output_tokens,
        "messages": messages,
    }
    system = _system_text(request.messages)
    if system:
        payload["system"] = system
    if route.capabilities.get("sampling_parameters") is not False:
        payload["temperature"] = request.temperature
    if request.response_schema:
        payload["output_config"] = {"format": {"type": "json_schema", "schema": _response_schema(request)}}
    if request.thinking_mode != "provider_default":
        payload["thinking"] = {"type": request.thinking_mode}
    _guard_request_size(payload)
    return payload


def _compiled_messages(
    request: ModelRequest,
    route: ModelInvocationRoute,
    media_resolver: ModelMediaContentResolver | None,
) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []
    for message in request.messages:
        compiled = _message_payload(message, request, route, media_resolver)
        if compiled is not None:
            messages.append(compiled)
    return messages


def parse_anthropic_response(
    request: ModelRequest,
    body: Mapping[str, object],
    headers: Mapping[str, str],
    *,
    latency_ms: int,
) -> ModelResponse:
    """Normalize a successful Anthropic response into the governed model contract."""

    route = _required_route(request)
    _guard_returned_model(body, route.provider_model_id)
    finish_reason = _finish_reason(request, body)
    text, tool_calls = _response_content(body)
    usage = _mapping(body.get("usage"))
    request_id = headers.get("request-id") or _text(body.get("id")) or ""
    return ModelResponse(
        provider="anthropic",
        resolved_model_id="",
        resolved_model_revision="",
        content=text,
        finish_reason=finish_reason,
        input_tokens=_integer(usage.get("input_tokens")),
        output_tokens=_integer(usage.get("output_tokens")),
        normalized_tool_calls=tool_calls,
        provider_request_id=request_id,
        latency_ms=latency_ms,
    )


def _guard_returned_model(body: Mapping[str, object], expected_model: str) -> None:
    returned_model = _text(body.get("model"))
    if returned_model and returned_model != expected_model:
        raise _failure(
            "conflict",
            "Anthropic returned a different model than the pinned request",
            "provider_model_mismatch",
        )


def _finish_reason(request: ModelRequest, body: Mapping[str, object]) -> str:
    finish_reason = _text(body.get("stop_reason")) or "unknown"
    if request.response_schema and finish_reason != "end_turn":
        reason = _structured_stop_reason(finish_reason)
        raise _failure(
            "validation",
            f"Anthropic structured output ended with stop_reason={finish_reason}",
            reason,
            details={
                "stopReason": finish_reason,
                "outputTokens": _integer(_mapping(body.get("usage")).get("output_tokens")),
                "providerRequestId": _text(body.get("id")),
            },
        )
    return finish_reason


def _structured_stop_reason(finish_reason: str) -> str:
    if finish_reason in {"max_tokens", "model_context_window_exceeded"}:
        return "structured_output_incomplete"
    if finish_reason == "refusal":
        return "structured_output_refused"
    return "structured_output_unexpected_stop"


def _response_content(
    body: Mapping[str, object],
) -> tuple[str, tuple[ModelToolCall, ...]]:
    content = _content_blocks(body.get("content"))
    text = "".join(_text(block.get("text")) for block in content if block["type"] == "text")
    tool_calls = tuple(_tool_call(block) for block in content if block["type"] == "tool_use")
    if not text and not tool_calls:
        raise _failure("validation", "Anthropic response contained no usable content", "empty_model_response")
    return text, tool_calls


def _message_payload(
    message: ModelMessage,
    request: ModelRequest,
    route: ModelInvocationRoute,
    media_resolver: ModelMediaContentResolver | None,
) -> dict[str, object] | None:
    if message.role == "system":
        if message.media_references:
            raise _validation("system messages cannot carry media references", "invalid_system_media")
        return None
    if message.role == "assistant" and message.media_references:
        raise _validation("assistant messages cannot carry media references", "invalid_assistant_media")
    blocks = [_media_block(reference, request, route, media_resolver) for reference in message.media_references]
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    if not blocks:
        raise _validation("model messages cannot be empty", "empty_message")
    return {"role": message.role, "content": blocks}


def _media_block(
    reference: ModelMediaReference,
    request: ModelRequest,
    route: ModelInvocationRoute,
    media_resolver: ModelMediaContentResolver | None,
) -> dict[str, object]:
    if media_resolver is None:
        raise _failure("unsupported", "no governed media resolver is configured", "media_resolver_unavailable")
    content = media_resolver.read(
        tenant_id=route.tenant_id,
        reference=reference,
        expected_classification=request.data_classification,
        allowed_classifications=request.media_allowed_classifications,
    )
    encoded = base64.standard_b64encode(content.content).decode("ascii")
    mime_type = _normalized_mime(content.mime_type)
    if mime_type == "application/pdf":
        return _document_block(encoded)
    if mime_type not in _IMAGE_MIME_TYPES:
        raise _failure("unsupported", f"Anthropic does not support media MIME {mime_type}", "media_mime_unsupported")
    if len(encoded.encode("ascii")) > _MAX_IMAGE_BASE64_BYTES:
        raise _validation("Anthropic image input exceeds the base64 size limit", "image_too_large")
    return _image_block(encoded, mime_type)


def _document_block(encoded: str) -> dict[str, object]:
    return {
        "type": "document",
        "source": {"type": "base64", "media_type": "application/pdf", "data": encoded},
    }


def _image_block(encoded: str, mime_type: str) -> dict[str, object]:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": mime_type, "data": encoded},
    }


def _system_text(messages: Sequence[ModelMessage]) -> str:
    return "\n\n".join(message.content for message in messages if message.role == "system" and message.content)


def _response_schema(request: ModelRequest) -> dict[str, object]:
    try:
        parsed = json.loads(request.response_schema or "")
    except json.JSONDecodeError as exc:
        raise _validation("model response schema is not valid JSON", "response_schema_invalid") from exc
    if not isinstance(parsed, Mapping):
        raise _validation("model response schema must be a JSON object", "response_schema_invalid")
    normalized = _normalize_schema_node(parsed)
    if not isinstance(normalized, dict):
        raise _validation("model response schema must be a JSON object", "response_schema_invalid")
    return normalized


def _normalize_schema_node(value: object) -> object:
    """Apply the same object strictness transformation as Anthropic SDK helpers."""

    if isinstance(value, Mapping):
        normalized = {str(key): _normalize_schema_node(item) for key, item in value.items()}
        if normalized.get("type") == "object":
            normalized["additionalProperties"] = False
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [_normalize_schema_node(item) for item in value]
    return value


def _content_blocks(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise _failure("validation", "Anthropic response content is malformed", "response_content_invalid")
    blocks: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        block_type = _text(item.get("type"))
        if block_type == "text" and isinstance(item.get("text"), str):
            blocks.append({"type": "text", "text": str(item["text"])})
        elif block_type == "tool_use":
            blocks.append({"type": "tool_use", **dict(item)})
    return blocks


def _tool_call(block: Mapping[str, object]) -> ModelToolCall:
    name = _text(block.get("name"))
    if not name:
        raise _failure("validation", "Anthropic tool call is missing a name", "tool_call_invalid")
    return ModelToolCall(tool_name=name, arguments_json=json.dumps(block.get("input", {}), sort_keys=True))


def _guard_request_size(payload: Mapping[str, object]) -> None:
    byte_size = len(json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
    if byte_size > _MAX_REQUEST_BYTES:
        raise _validation("Anthropic request exceeds the 32 MiB request limit", "request_too_large")


def _required_route(request: ModelRequest) -> ModelInvocationRoute:
    route = request.resolved_route
    if route is None:
        raise _failure("validation", "language-model request is missing a gateway-resolved route", "route_required")
    if route.provider_profile != "anthropic":
        raise _failure("unsupported", "Anthropic adapter received a different provider profile", "provider_mismatch")
    return route


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _integer(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _normalized_mime(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def _validation(message: str, reason: str) -> AdapterError:
    return _failure("validation", message, reason)


def _failure(
    kind: AdapterFailureKind,
    message: str,
    reason: str,
    *,
    details: Mapping[str, object] | None = None,
) -> AdapterError:
    return AdapterError(
        AdapterFailure(
            adapter_profile="anthropic",
            operation="complete",
            kind=kind,
            is_retryable=False,
            operator_message=message,
            details={"reason": reason, **dict(details or {})},
        )
    )
