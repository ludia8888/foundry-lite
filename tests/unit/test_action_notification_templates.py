"""Deterministic pre-commit notification template contract."""

import pytest
from foundry_lite.domain.action_runtime.action_effects import compile_action_effects
from foundry_lite.domain.action_runtime.action_notification_templates import (
    render_notification_template_payload,
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
