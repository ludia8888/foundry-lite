"""SSE streaming and form-field parsing helpers shared across routers."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator

from foundry_lite.domain.errors import ValidationFailed

from foundry_lite_api.schemas import JsonObject


def _sse_json_events(events: Iterable[JsonObject]) -> Iterator[str]:
    for payload in events:
        yield _sse_json_event(payload)


def _sse_json_event(payload: JsonObject) -> str:
    name = str(payload.get("event", "message"))
    return f"event: {name}\ndata: {json.dumps(payload, sort_keys=True)}\n\n"


def _with_first_event(first: JsonObject, events: Iterable[JsonObject]) -> Iterator[JsonObject]:
    yield first
    yield from events


def _json_form_object(value: str, field_name: str) -> JsonObject:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValidationFailed("form field must be a JSON object", details={"field": field_name}) from exc
    if not isinstance(parsed, dict):
        raise ValidationFailed("form field must be a JSON object", details={"field": field_name})
    return {str(key): item for key, item in parsed.items()}


def _optional_json_form_object(value: str | None, field_name: str) -> JsonObject | None:
    if value is None or not value.strip():
        return None
    return _json_form_object(value, field_name)


def _json_form_string_list(value: str, field_name: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValidationFailed("form field must be a JSON string array", details={"field": field_name}) from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValidationFailed("form field must be a JSON string array", details={"field": field_name})
    return [item for item in parsed if item]
