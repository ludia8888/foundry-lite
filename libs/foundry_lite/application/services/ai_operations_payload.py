"""Application service helpers for ai operations payload workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import cast

from foundry_lite.application.ports import RuntimeJsonObject, RuntimeRow
from foundry_lite.application.ports.ai_run_repository import AiLedgerRow, AiRunLedgerSnapshot

_REDACTED = "[redacted]"
_FORBIDDEN_JSON_KEYS = frozenset(
    {
        "apikey",
        "arguments",
        "authorization",
        "body",
        "completion",
        "content",
        "password",
        "prompt",
        "prompttext",
        "rawinput",
        "rawoutput",
        "rawprompt",
        "rawrequest",
        "rawresponse",
        "rawtoolresult",
        "requestbody",
        "response",
        "responsebody",
        "secret",
        "systemprompt",
        "text",
        "token",
        "toolarguments",
        "toolresult",
        "userprompt",
    }
)

_AI_RUN_FIELDS = (
    "id",
    "tenant_id",
    "session_id",
    "agent_version_id",
    "actor_user_id",
    "request_id",
    "trace_id",
    "status",
    "ontology_version_id",
    "model_alias_version",
    "resolved_model_id",
    "resolved_model_revision",
    "prompt_version_id",
    "compiled_prompt_hash",
    "tool_manifest_hash",
    "context_manifest_hash",
    "state_snapshot_hash",
    "policy_snapshot_hash",
    "budget_json",
    "usage_json",
    "error_json",
    "started_at",
    "completed_at",
)
_AI_EVENT_FIELDS = (
    "id",
    "ai_run_id",
    "sequence",
    "event_type",
    "payload_ref",
    "payload_hash",
    "redacted_preview",
    "created_at",
)
_AI_MODEL_CALL_FIELDS = (
    "id",
    "ai_run_id",
    "attempt",
    "provider",
    "model_revision",
    "request_hash",
    "response_hash",
    "provider_request_id",
    "input_tokens",
    "output_tokens",
    "latency_ms",
    "status",
    "error_json",
)
_AI_CONTEXT_ITEM_FIELDS = (
    "id",
    "ai_run_id",
    "context_id",
    "kind",
    "source_resource_type",
    "source_resource_id",
    "source_version",
    "content_hash",
    "retrieval_method",
    "relevance_score",
    "security_partition",
    "token_estimate",
    "selected",
    "omission_reason",
)
_AI_TOOL_CALL_FIELDS = (
    "id",
    "ai_run_id",
    "sequence",
    "tool_id",
    "tool_version",
    "arguments_hash",
    "effect",
    "authorization_decision",
    "confirmation_policy",
    "status",
    "result_hash",
    "linked_action_run_id",
    "started_at",
    "completed_at",
    "error_json",
    "result_json",
)
_AI_CITATION_FIELDS = (
    "id",
    "ai_run_id",
    "message_id",
    "context_item_id",
    "claim_span",
    "citation_order",
    "rendered_ref",
)
_AI_USAGE_FIELDS = (
    "id",
    "ai_run_id",
    "provider",
    "model_revision",
    "input_tokens",
    "output_tokens",
    "estimated_cost",
    "currency",
    "recorded_at",
)
_AI_PROMPT_ARTIFACT_FIELDS = (
    "id",
    "ai_run_id",
    "artifact_kind",
    "artifact_ref",
    "content_hash",
    "artifact_hash",
    "byte_size",
    "encryption_key_ref",
    "encryption_algorithm",
    "retention_until",
    "legal_hold",
    "erasure_request_id",
    "export_marking",
    "created_at",
)


def safe_ai_run_row(row: Mapping[str, object]) -> RuntimeRow:
    """Return the AI execution row shape that Operations may expose directly."""

    return cast(RuntimeRow, _select_fields(row, _AI_RUN_FIELDS))


def ai_operations_payload(ledger: AiRunLedgerSnapshot) -> RuntimeJsonObject:
    """Build an operator-facing AI run detail with refs/hashes, never raw prompt/tool data."""

    run = _select_fields(ledger["run"], _AI_RUN_FIELDS)
    events = _select_rows(ledger["events"], _AI_EVENT_FIELDS)
    model_calls = _select_rows(ledger["modelCalls"], _AI_MODEL_CALL_FIELDS)
    context_items = _select_rows(ledger["contextItems"], _AI_CONTEXT_ITEM_FIELDS)
    tool_calls = _select_rows(ledger["toolCalls"], _AI_TOOL_CALL_FIELDS)
    citations = _select_rows(ledger["citations"], _AI_CITATION_FIELDS)
    usage = _select_rows(ledger["usage"], _AI_USAGE_FIELDS)
    prompt_artifacts = _select_rows(ledger["promptArtifacts"], _AI_PROMPT_ARTIFACT_FIELDS)
    return {
        "run": run,
        "events": events,
        "modelCalls": model_calls,
        "contextItems": context_items,
        "toolCalls": tool_calls,
        "citations": citations,
        "usage": usage,
        "promptArtifacts": prompt_artifacts,
        "summary": _summary(run, events, model_calls, context_items, tool_calls, citations, usage, prompt_artifacts),
        "trace": {
            "traceId": run.get("trace_id"),
            "timeline": _timeline(events, model_calls, tool_calls),
        },
    }


def _select_rows(rows: Sequence[AiLedgerRow], fields: Sequence[str]) -> list[dict[str, object]]:
    return [_select_fields(row, fields) for row in rows]


def _select_fields(row: Mapping[str, object], fields: Sequence[str]) -> dict[str, object]:
    return {field: _sanitize_value(row[field]) for field in fields if field in row and row[field] is not None}


def _sanitize_value(value: object) -> object:
    if isinstance(value, Mapping):
        sanitized: dict[str, object] = {}
        for key, item in value.items():
            safe_key = str(key)
            sanitized[safe_key] = _REDACTED if _is_forbidden_json_key(safe_key) else _sanitize_value(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    return value


def _is_forbidden_json_key(key: str) -> bool:
    normalized = "".join(char for char in key.lower() if char.isalnum())
    return normalized.startswith("raw") or normalized in _FORBIDDEN_JSON_KEYS


def _summary(
    run: Mapping[str, object],
    events: Sequence[Mapping[str, object]],
    model_calls: Sequence[Mapping[str, object]],
    context_items: Sequence[Mapping[str, object]],
    tool_calls: Sequence[Mapping[str, object]],
    citations: Sequence[Mapping[str, object]],
    usage: Sequence[Mapping[str, object]],
    prompt_artifacts: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    selected_context = sum(1 for item in context_items if item.get("selected") is True)
    input_tokens = _sum_number(usage, "input_tokens") or _sum_number(model_calls, "input_tokens")
    output_tokens = _sum_number(usage, "output_tokens") or _sum_number(model_calls, "output_tokens")
    estimated_cost = _sum_float(usage, "estimated_cost")
    currencies = sorted({str(row["currency"]) for row in usage if row.get("currency")})
    return {
        "status": run.get("status"),
        "startedAt": run.get("started_at"),
        "completedAt": run.get("completed_at"),
        "eventCount": len(events),
        "modelCallCount": len(model_calls),
        "contextItemCount": len(context_items),
        "selectedContextItemCount": selected_context,
        "omittedContextItemCount": len(context_items) - selected_context,
        "toolCallCount": len(tool_calls),
        "citationCount": len(citations),
        "usageLedgerCount": len(usage),
        "promptArtifactCount": len(prompt_artifacts),
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "estimatedCost": estimated_cost,
        "currency": currencies[0] if len(currencies) == 1 else ("mixed" if currencies else None),
        "latencyMs": _sum_number(model_calls, "latency_ms"),
    }


def _timeline(
    events: Sequence[Mapping[str, object]],
    model_calls: Sequence[Mapping[str, object]],
    tool_calls: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    timeline = _event_timeline(events) + _model_call_timeline(model_calls) + _tool_call_timeline(tool_calls)
    return sorted(timeline, key=_timeline_sort_key)


def _event_timeline(events: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "kind": "event",
            "at": event.get("created_at"),
            "order": event.get("sequence"),
            "label": event.get("event_type"),
            "payloadRef": event.get("payload_ref"),
            "payloadHash": event.get("payload_hash"),
            "preview": event.get("redacted_preview"),
        }
        for event in events
    ]


def _model_call_timeline(model_calls: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "kind": "model_call",
            "at": None,
            "order": model_call.get("attempt"),
            "label": model_call.get("model_revision"),
            "status": model_call.get("status"),
            "latencyMs": model_call.get("latency_ms"),
            "requestHash": model_call.get("request_hash"),
            "responseHash": model_call.get("response_hash"),
        }
        for model_call in model_calls
    ]


def _tool_call_timeline(tool_calls: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "kind": "tool_call",
            "at": tool_call.get("started_at"),
            "order": tool_call.get("sequence"),
            "label": tool_call.get("tool_id"),
            "status": tool_call.get("status"),
            "argumentsHash": tool_call.get("arguments_hash"),
            "resultHash": tool_call.get("result_hash"),
        }
        for tool_call in tool_calls
    ]


def _timeline_sort_key(item: Mapping[str, object]) -> tuple[str, int]:
    at = item.get("at")
    order = item.get("order")
    timestamp = at if isinstance(at, str) else "9999-12-31T23:59:59Z"
    return timestamp, int(order) if isinstance(order, int) else 0


def _sum_number(rows: Sequence[Mapping[str, object]], key: str) -> int:
    return sum(int(value) for row in rows if isinstance((value := row.get(key)), int))


def _sum_float(rows: Sequence[Mapping[str, object]], key: str) -> float:
    return sum(float(value) for row in rows if isinstance((value := row.get(key)), int | float))
