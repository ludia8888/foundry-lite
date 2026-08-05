"""Typed and fail-closed side-effect declarations for Action Contract v3."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from foundry_lite.domain.action_runtime.action_notification_templates import (
    validate_notification_template_payload,
)
from foundry_lite.domain.errors import ValidationFailed

ACTION_EFFECT_KINDS = frozenset({"webhook", "notification", "event", "schedule_build", "connector_command"})
ACTION_EFFECT_PHASES = frozenset({"before_commit", "after_commit"})
ACTION_EFFECT_RESPONSE_TYPES = frozenset(
    {"string", "boolean", "integer", "long", "float", "decimal", "date", "timestamp"}
)


@dataclass(frozen=True, slots=True)
class ActionEffectV3:
    """Canonical registered side-effect contract embedded in Action v3."""

    effect_id: str
    kind: str
    phase: str
    target_ref: str
    payload: Mapping[str, object]
    response_fields: Mapping[str, str]
    max_attempts: int
    timeout_seconds: int


def compile_action_effects(definition: Mapping[str, object]) -> tuple[ActionEffectV3, ...]:
    """Normalize v3 effects and legacy writebacks into one validated contract."""
    if "effects" in definition:
        effects = tuple(_effect(item, index) for index, item in enumerate(_sequence(definition.get("effects"))))
    else:
        effects = _legacy_effects(definition)
    _validate_effect_set(effects)
    return effects


def action_effect_payload(effect: ActionEffectV3) -> dict[str, object]:
    """Serialize an effect deterministically for fingerprints and deployment plans."""
    return {
        "effectId": effect.effect_id,
        "kind": effect.kind,
        "phase": effect.phase,
        "targetRef": effect.target_ref,
        "payload": dict(effect.payload),
        "responseFields": dict(effect.response_fields),
        "maxAttempts": effect.max_attempts,
        "timeoutSeconds": effect.timeout_seconds,
    }


def validate_action_effect_response(effect: ActionEffectV3, response: Mapping[str, object]) -> None:
    """Validate every response field before it may influence an Ontology EditPlan."""
    for field, data_type in effect.response_fields.items():
        if field not in response:
            raise ValidationFailed(
                "before-commit webhook response is missing a declared field",
                details={"effectId": effect.effect_id, "field": field},
            )
        if not _matches_response_type(response[field], data_type):
            raise ValidationFailed(
                "before-commit webhook response field has the wrong type",
                details={"effectId": effect.effect_id, "field": field, "type": data_type},
            )


def _matches_response_type(value: object, data_type: str) -> bool:
    """Match a webhook response value against an allowed scalar type."""
    if data_type == "boolean":
        return isinstance(value, bool)
    if data_type in {"integer", "long"}:
        return isinstance(value, int) and not isinstance(value, bool)
    if data_type in {"float", "decimal"}:
        return isinstance(value, int | float) and not isinstance(value, bool)
    return isinstance(value, str)


def _legacy_effects(definition: Mapping[str, object]) -> tuple[ActionEffectV3, ...]:
    """Normalize legacy writeback and side-effect declarations."""
    result: list[ActionEffectV3] = []
    for index, raw in enumerate(_sequence(definition.get("writebacks"))):
        item = _mapping(raw, "writeback")
        writeback_definition: dict[str, object] = {
            "effectId": item.get("apiName") or f"writeback-{index + 1}",
            "kind": "webhook",
            "phase": "before_commit",
            "targetRef": item.get("connector"),
            "payload": item.get("payload") or {},
            "maxAttempts": 1,
        }
        result.append(_effect(writeback_definition, index))
    offset = len(result)
    for index, raw in enumerate(_sequence(definition.get("sideEffects"))):
        item = _mapping(raw, "side effect")
        kind = _kind(item.get("kind") or item.get("type"))
        side_effect_definition: dict[str, object] = {
            "effectId": item.get("apiName") or f"{kind}-{index + 1}",
            "kind": kind,
            "phase": "after_commit",
            "targetRef": _legacy_target_ref(item, kind),
            "payload": item.get("payload") or {},
        }
        result.append(_effect(side_effect_definition, offset + index))
    return tuple(result)


def _effect(raw: object, index: int) -> ActionEffectV3:
    """Compile one registered destination into a bounded effect contract."""
    item = _mapping(raw, "effect")
    _reject_inline_destination(item)
    kind = _kind(item.get("kind") or item.get("type"))
    phase = _phase(item.get("phase") or item.get("mode"))
    effect_id = _text(item.get("effectId") or item.get("apiName"), f"effect-{index + 1}")
    target_ref = _text(item.get("targetRef") or item.get("connector"), "")
    if not target_ref:
        raise ValidationFailed("action effect requires a registered targetRef", details={"effectId": effect_id})
    max_attempts = _bounded_int(item.get("maxAttempts"), 1 if phase == "before_commit" else 3, 1, 10)
    timeout_seconds = _bounded_int(item.get("timeoutSeconds"), 30, 1, 300)
    payload = _mapping_or_empty(item.get("payload"))
    if kind == "notification":
        validate_notification_template_payload(payload)
    return ActionEffectV3(
        effect_id=effect_id,
        kind=kind,
        phase=phase,
        target_ref=target_ref,
        payload=payload,
        response_fields=_response_fields(item.get("responseFields")),
        max_attempts=max_attempts,
        timeout_seconds=timeout_seconds,
    )


def _validate_effect_set(effects: tuple[ActionEffectV3, ...]) -> None:
    """Apply cross-effect uniqueness, phase, and response invariants."""
    _validate_unique_effect_ids(effects)
    _validate_before_effects(effects)
    _validate_effect_response_fields(effects)


def _validate_unique_effect_ids(effects: tuple[ActionEffectV3, ...]) -> None:
    """Reject duplicate effect identifiers within one Action."""
    ids = [effect.effect_id for effect in effects]
    if len(ids) != len(set(ids)):
        raise ValidationFailed("duplicate action effect id", details={"effectIds": ids})


def _validate_before_effects(effects: tuple[ActionEffectV3, ...]) -> None:
    """Allow at most one before-commit webhook gate."""
    before = [effect for effect in effects if effect.phase == "before_commit"]
    if len(before) > 1:
        raise ValidationFailed("an Action may declare only one before-commit effect")
    if before and before[0].kind != "webhook":
        raise ValidationFailed("before-commit Action effect must be a webhook")


def _validate_effect_response_fields(effects: tuple[ActionEffectV3, ...]) -> None:
    """Restrict typed response mappings to the before-commit phase."""
    for effect in effects:
        if effect.response_fields and effect.phase != "before_commit":
            raise ValidationFailed(
                "Action effect response fields are only supported for before-commit webhooks",
                details={"effectId": effect.effect_id},
            )


def _response_fields(raw: object) -> Mapping[str, str]:
    """Validate declared webhook response field types."""
    values = _mapping_or_empty(raw)
    result: dict[str, str] = {}
    for field, data_type in values.items():
        if not field or data_type not in ACTION_EFFECT_RESPONSE_TYPES:
            raise ValidationFailed(
                "Action effect response field has an unsupported type",
                details={"field": field, "type": data_type},
            )
        result[field] = str(data_type)
    return result


def _reject_inline_destination(value: object, path: str = "effect") -> None:
    """Reject request-controlled URLs recursively to prevent SSRF."""
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        for key, child in mapping.items():
            normalized = str(key).replace("_", "").lower()
            if normalized in {"url", "uri", "endpoint", "destination"}:
                raise ValidationFailed(
                    "action effects cannot declare an inline destination; use targetRef",
                    details={"field": f"{path}.{key}"},
                )
            _reject_inline_destination(child, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for index, child in enumerate(cast(Sequence[object], value)):
            _reject_inline_destination(child, f"{path}[{index}]")


def _legacy_target_ref(item: Mapping[str, object], kind: str) -> object:
    """Resolve a legacy effect's registered connector or policy reference."""
    keys = {
        "event": ("topic",),
        "notification": ("channel", "recipientPolicy"),
        "schedule_build": ("schedule", "build"),
        "connector_command": ("connector",),
        "webhook": ("connector",),
    }
    for key in keys[kind]:
        if item.get(key):
            return item[key]
    return None


def _kind(raw: object) -> str:
    """Normalize and validate an effect kind."""
    aliases = {
        "schedule": "schedule_build",
        "build": "schedule_build",
        "scheduleBuild": "schedule_build",
        "connectorCommand": "connector_command",
    }
    value = aliases.get(str(raw), str(raw))
    if value not in ACTION_EFFECT_KINDS:
        raise ValidationFailed("unsupported action effect kind", details={"kind": raw})
    return value


def _phase(raw: object) -> str:
    """Normalize and validate an effect phase."""
    aliases = {"beforeCommit": "before_commit", "afterCommit": "after_commit"}
    value = aliases.get(str(raw), str(raw or "after_commit"))
    if value not in ACTION_EFFECT_PHASES:
        raise ValidationFailed("unsupported action effect phase", details={"phase": raw})
    return value


def _mapping(raw: object, label: str) -> Mapping[str, object]:
    """Require a mapping-shaped effect field."""
    if not isinstance(raw, Mapping):
        raise ValidationFailed(f"action {label} must be an object")
    return cast(Mapping[str, object], raw)


def _mapping_or_empty(raw: object) -> Mapping[str, object]:
    """Normalize an optional mapping-shaped effect field."""
    return {} if raw is None else _mapping(raw, "effect payload")


def _sequence(raw: object) -> tuple[object, ...]:
    """Normalize an optional effect sequence without accepting strings."""
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise ValidationFailed("action effects must be a list")
    return tuple(cast(Sequence[object], raw))


def _text(raw: object, default: str) -> str:
    """Return non-empty trimmed text or a deterministic default."""
    return raw.strip() if isinstance(raw, str) and raw.strip() else default


def _bounded_int(raw: object, default: int, minimum: int, maximum: int) -> int:
    """Validate a bounded integer execution policy value."""
    value = default if raw is None else raw
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValidationFailed(
            "action effect integer policy is outside its allowed range",
            details={"value": value, "minimum": minimum, "maximum": maximum},
        )
    return value
