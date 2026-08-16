"""Fail-closed edge cases for registered Action effects."""

from __future__ import annotations

import pytest
from foundry_lite.domain.action_runtime.action_effects import (
    ActionEffectV3,
    action_effect_payload,
    compile_action_effects,
    validate_action_effect_response,
)
from foundry_lite.domain.errors import ValidationFailed


def _effect(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "effectId": "verify-order",
        "kind": "webhook",
        "phase": "before_commit",
        "targetRef": "connector.verify-order",
        "payload": {"orderId": "O-1"},
        "responseFields": {"approved": "boolean", "score": "decimal", "attempt": "integer"},
        "maxAttempts": 1,
        "timeoutSeconds": 30,
    }
    value.update(overrides)
    return value


def test_compiled_effect_serializes_exact_registered_contract() -> None:
    effect = compile_action_effects({"effects": [_effect()]})[0]

    assert action_effect_payload(effect) == {
        "effectId": "verify-order",
        "kind": "webhook",
        "phase": "before_commit",
        "targetRef": "connector.verify-order",
        "payload": {"orderId": "O-1"},
        "responseFields": {"approved": "boolean", "score": "decimal", "attempt": "integer"},
        "maxAttempts": 1,
        "timeoutSeconds": 30,
    }
    validate_action_effect_response(effect, {"approved": True, "score": "1.5", "attempt": 2})


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({"approved": True, "score": "1.5"}, "missing a declared field"),
        ({"approved": 1, "score": "1.5", "attempt": 2}, "wrong type"),
        ({"approved": True, "score": True, "attempt": 2}, "wrong type"),
        ({"approved": True, "score": 1.5, "attempt": 2}, "wrong type"),
        ({"approved": True, "score": "1.5", "attempt": False}, "wrong type"),
    ],
)
def test_response_validation_rejects_missing_fields_and_bool_numeric_confusion(
    response: dict[str, object],
    message: str,
) -> None:
    effect = compile_action_effects({"effects": [_effect()]})[0]

    with pytest.raises(ValidationFailed, match=message):
        validate_action_effect_response(effect, response)


@pytest.mark.parametrize(
    ("effects", "message"),
    [
        ([_effect(targetRef="")], "registered targetRef"),
        ([_effect(), _effect()], "duplicate action effect id"),
        ([_effect(), _effect(effectId="second")], "only one before-commit"),
        ([_effect(kind="event")], "before-commit Action effect must be a webhook"),
        ([_effect(phase="after_commit")], "response fields are only supported"),
        ([_effect(responseFields={"result": "object"})], "unsupported type"),
        ([_effect(kind="unknown")], "unsupported action effect kind"),
        ([_effect(phase="during_commit")], "unsupported action effect phase"),
        ([_effect(maxAttempts=True)], "integer policy"),
        ([_effect(timeoutSeconds=301)], "integer policy"),
    ],
)
def test_effect_compiler_rejects_ambiguous_or_unsafe_execution_contracts(
    effects: list[dict[str, object]],
    message: str,
) -> None:
    with pytest.raises(ValidationFailed, match=message):
        compile_action_effects({"effects": effects})


@pytest.mark.parametrize(
    "payload",
    [
        {"url": "https://attacker.invalid"},
        {"nested": [{"destination": "https://attacker.invalid"}]},
        {"nested": {"end_point": "https://attacker.invalid"}},
    ],
)
def test_effect_payload_recursively_rejects_request_controlled_destinations(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationFailed, match="cannot declare an inline destination"):
        compile_action_effects({"effects": [_effect(payload=payload)]})


def test_legacy_effects_resolve_registered_targets_and_reject_missing_target() -> None:
    compiled = compile_action_effects(
        {
            "writebacks": [{"apiName": "verify", "connector": "connector.verify"}],
            "sideEffects": [{"apiName": "publish", "kind": "event", "topic": "topic.orders"}],
        }
    )
    assert [(effect.kind, effect.phase, effect.target_ref) for effect in compiled] == [
        ("webhook", "before_commit", "connector.verify"),
        ("event", "after_commit", "topic.orders"),
    ]

    with pytest.raises(ValidationFailed, match="registered targetRef"):
        compile_action_effects({"sideEffects": [{"kind": "connector_command"}]})


def test_effects_field_is_strictly_a_list_of_objects() -> None:
    with pytest.raises(ValidationFailed, match="must be a list"):
        compile_action_effects({"effects": "webhook"})
    with pytest.raises(ValidationFailed, match="must be an object"):
        compile_action_effects({"effects": ["webhook"]})
    with pytest.raises(ValidationFailed, match="effect payload must be an object"):
        compile_action_effects({"effects": [_effect(payload=[])]})


def test_string_response_types_accept_text_only() -> None:
    effect = ActionEffectV3(
        effect_id="date-result",
        kind="webhook",
        phase="before_commit",
        target_ref="connector.date",
        payload={},
        response_fields={"date": "date"},
        max_attempts=1,
        timeout_seconds=30,
    )
    validate_action_effect_response(effect, {"date": "2026-08-13"})
    with pytest.raises(ValidationFailed, match="wrong type"):
        validate_action_effect_response(effect, {"date": 20260813})


@pytest.mark.parametrize(
    ("data_type", "value"),
    [
        ("date", "2026-02-30"),
        ("timestamp", "2026-08-13T12:00:00"),
        ("float", float("nan")),
        ("decimal", float("inf")),
    ],
)
def test_webhook_response_rejects_invalid_temporal_and_non_finite_numeric_values(
    data_type: str,
    value: object,
) -> None:
    effect = ActionEffectV3(
        effect_id="typed-result",
        kind="webhook",
        phase="before_commit",
        target_ref="connector.typed",
        payload={},
        response_fields={"result": data_type},
        max_attempts=1,
        timeout_seconds=30,
    )

    with pytest.raises(ValidationFailed, match="wrong type"):
        validate_action_effect_response(effect, {"result": value})
