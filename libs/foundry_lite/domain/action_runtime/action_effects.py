"""Typed and fail-closed side-effect declarations for Action Contract v3."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from foundry_lite.domain.errors import ValidationFailed

ACTION_EFFECT_KINDS = frozenset({"webhook", "notification", "event", "schedule_build", "connector_command"})
ACTION_EFFECT_PHASES = frozenset({"before_commit", "after_commit"})


@dataclass(frozen=True, slots=True)
class ActionEffectV3:
    """Canonical registered side-effect contract embedded in Action v3."""

    effect_id: str
    kind: str
    phase: str
    target_ref: str
    payload: Mapping[str, object]
    max_attempts: int
    timeout_seconds: int


def compile_action_effects(definition: Mapping[str, object]) -> tuple[ActionEffectV3, ...]:
    """Normalize v3 effects and legacy writebacks into one validated contract."""
    """Normalize v3 effects plus v1/v2 writebacks and sideEffects."""
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
        "maxAttempts": effect.max_attempts,
        "timeoutSeconds": effect.timeout_seconds,
    }


def _legacy_effects(definition: Mapping[str, object]) -> tuple[ActionEffectV3, ...]:
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
    return ActionEffectV3(
        effect_id=effect_id,
        kind=kind,
        phase=phase,
        target_ref=target_ref,
        payload=_mapping_or_empty(item.get("payload")),
        max_attempts=max_attempts,
        timeout_seconds=timeout_seconds,
    )


def _validate_effect_set(effects: tuple[ActionEffectV3, ...]) -> None:
    ids = [effect.effect_id for effect in effects]
    if len(ids) != len(set(ids)):
        raise ValidationFailed("duplicate action effect id", details={"effectIds": ids})
    before = [effect for effect in effects if effect.phase == "before_commit"]
    if len(before) > 1:
        raise ValidationFailed("an Action may declare only one before-commit effect")
    if before and before[0].kind != "webhook":
        raise ValidationFailed("before-commit Action effect must be a webhook")


def _reject_inline_destination(value: object, path: str = "effect") -> None:
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
    aliases = {"beforeCommit": "before_commit", "afterCommit": "after_commit"}
    value = aliases.get(str(raw), str(raw or "after_commit"))
    if value not in ACTION_EFFECT_PHASES:
        raise ValidationFailed("unsupported action effect phase", details={"phase": raw})
    return value


def _mapping(raw: object, label: str) -> Mapping[str, object]:
    if not isinstance(raw, Mapping):
        raise ValidationFailed(f"action {label} must be an object")
    return cast(Mapping[str, object], raw)


def _mapping_or_empty(raw: object) -> Mapping[str, object]:
    return {} if raw is None else _mapping(raw, "effect payload")


def _sequence(raw: object) -> tuple[object, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        raise ValidationFailed("action effects must be a list")
    return tuple(cast(Sequence[object], raw))


def _text(raw: object, default: str) -> str:
    return raw.strip() if isinstance(raw, str) and raw.strip() else default


def _bounded_int(raw: object, default: int, minimum: int, maximum: int) -> int:
    value = default if raw is None else raw
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValidationFailed(
            "action effect integer policy is outside its allowed range",
            details={"value": value, "minimum": minimum, "maximum": maximum},
        )
    return value
