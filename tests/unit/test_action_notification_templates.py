"""Deterministic pre-commit notification template contract."""

import pytest
from foundry_lite.domain.action_runtime.action_effects import compile_action_effects
from foundry_lite.domain.action_runtime.action_notification_templates import (
    render_notification_template_payload,
    validate_notification_template_payload,
)
from foundry_lite.domain.errors import ValidationFailed


def test_notification_template_renders_typed_and_embedded_pre_commit_values() -> None:
    rendered = render_notification_template_payload(
        {
            "title": "Order {{object.status}}",
            "quantity": "{{parameters.quantity}}",
            "actor": "{{actor.userId}}",
            "metadata": {"run": "{{action.runId}}"},
        },
        object_properties={"status": "PENDING"},
        parameters={"quantity": 3},
        actor_user_id="operator-1",
        action_run_id="run-1",
        action_api_name="ApproveOrder",
    )

    assert rendered == {
        "title": "Order PENDING",
        "quantity": 3,
        "actor": "operator-1",
        "metadata": {"run": "run-1"},
    }


def test_notification_template_recurses_through_lists_and_preserves_literal_values() -> None:
    validate_notification_template_payload({"items": ["literal", {"status": "{{object.status}}"}]})
    rendered = render_notification_template_payload(
        {
            "items": [
                "{{object.status}}",
                {"label": "Quantity {{parameters.quantity}}"},
                7,
            ]
        },
        object_properties={"status": "PENDING"},
        parameters={"quantity": 3},
        actor_user_id="operator-1",
        action_run_id="run-1",
        action_api_name="ApproveOrder",
    )

    assert rendered == {"items": ["PENDING", {"label": "Quantity 3"}, 7]}


def test_notification_template_rejects_embedding_an_object_inside_text() -> None:
    with pytest.raises(ValidationFailed, match="embedded placeholder must resolve to a scalar"):
        render_notification_template_payload(
            {"body": "Order metadata: {{object.metadata}}"},
            object_properties={"metadata": {"region": "seoul"}},
            parameters={},
            actor_user_id="operator-1",
            action_run_id="run-1",
            action_api_name="ApproveOrder",
        )


@pytest.mark.parametrize(
    "template",
    ("{{secret.token}}", "{{object.status", "{{object.missing}}"),
)
def test_notification_template_fails_closed_for_invalid_or_unavailable_values(template: str) -> None:
    definition = {
        "effects": [
            {
                "effectId": "notify",
                "kind": "notification",
                "phase": "after_commit",
                "targetRef": "notification-policy:operations",
                "payload": {"body": template},
            }
        ]
    }
    if template != "{{object.missing}}":
        with pytest.raises(ValidationFailed):
            compile_action_effects(definition)
        return
    effect = compile_action_effects(definition)[0]
    with pytest.raises(ValidationFailed):
        render_notification_template_payload(
            effect.payload,
            object_properties={"status": "PENDING"},
            parameters={},
            actor_user_id="operator-1",
            action_run_id="run-1",
            action_api_name="ApproveOrder",
        )
