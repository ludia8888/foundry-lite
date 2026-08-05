"""Typed, deterministic notification templates resolved before Ontology edits commit."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.domain.errors import ValidationFailed

_TOKEN = re.compile(r"{{\s*((?:object|parameters|actor|action)\.[A-Za-z_][A-Za-z0-9_.]*)\s*}}")


def validate_notification_template_payload(payload: Mapping[str, object]) -> None:
    """Reject malformed or ungoverned placeholders when the Action is compiled."""
    _validate_value(payload, "payload")


def render_notification_template_payload(
    payload: Mapping[str, object],
    *,
    object_properties: Mapping[str, object],
    parameters: Mapping[str, object],
    actor_user_id: str,
    action_run_id: str,
    action_api_name: str,
) -> dict[str, object]:
    """Resolve a notification payload from a pre-commit target snapshot."""
    context: Mapping[str, object] = {
        "object": object_properties,
        "parameters": parameters,
        "actor": {"userId": actor_user_id},
        "action": {"runId": action_run_id, "apiName": action_api_name},
    }
    rendered = _render_value(payload, context, "payload")
    if not isinstance(rendered, Mapping):
        raise ValidationFailed("notification payload must remain an object after rendering")
    return dict(cast(Mapping[str, object], rendered))


def _validate_value(value: object, path: str) -> None:
    if isinstance(value, str):
        _validate_text(value, path)
        return
    if isinstance(value, Mapping):
        for key, child in cast(Mapping[object, object], value).items():
            _validate_value(child, f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        for index, child in enumerate(cast(Sequence[object], value)):
            _validate_value(child, f"{path}[{index}]")


def _validate_text(value: str, path: str) -> None:
    if "{{" not in value and "}}" not in value:
        return
    remainder = _TOKEN.sub("", value)
    if "{{" in remainder or "}}" in remainder or not _TOKEN.search(value):
        raise ValidationFailed(
            "notification payload contains an invalid template placeholder",
            details={"path": path},
        )


def _render_value(value: object, context: Mapping[str, object], path: str) -> object:
    if isinstance(value, str):
        return _render_text(value, context, path)
    if isinstance(value, Mapping):
        return {
            str(key): _render_value(child, context, f"{path}.{key}")
            for key, child in cast(Mapping[object, object], value).items()
        }
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        children = cast(Sequence[object], value)
        return [_render_value(child, context, f"{path}[{index}]") for index, child in enumerate(children)]
    return value


def _render_text(value: str, context: Mapping[str, object], path: str) -> object:
    _validate_text(value, path)
    exact = _TOKEN.fullmatch(value)
    if exact:
        return _resolve(exact.group(1), context, path)

    def replace(match: re.Match[str]) -> str:
        resolved = _resolve(match.group(1), context, path)
        if isinstance(resolved, Mapping | Sequence) and not isinstance(resolved, str | bytes):
            raise ValidationFailed(
                "notification embedded placeholder must resolve to a scalar",
                details={"path": path, "placeholder": match.group(1)},
            )
        return str(resolved)

    return _TOKEN.sub(replace, value)


def _resolve(coordinate: str, context: Mapping[str, object], path: str) -> object:
    root, *segments = coordinate.split(".")
    current = context[root]
    for segment in segments:
        if not isinstance(current, Mapping) or segment not in current:
            raise ValidationFailed(
                "notification template value is unavailable in the pre-commit snapshot",
                details={"path": path, "placeholder": coordinate},
            )
        current = cast(Mapping[str, object], current)[segment]
    return current
