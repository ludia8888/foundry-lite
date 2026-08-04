from __future__ import annotations

import pytest
from foundry_lite.application.ports.action_effect_executor import (
    ActionEffectExecutionRequest,
    ActionEffectExecutionResult,
    ActionEffectPermanentError,
)
from foundry_lite.domain.action_runtime.action_effects import compile_action_effects
from foundry_lite.infrastructure.adapters.action_effect_executor import AllowlistedActionEffectExecutor


def test_action_effect_executor_requires_registered_target_and_preserves_idempotency_coordinate() -> None:
    adapter = AllowlistedActionEffectExecutor()
    observed: list[ActionEffectExecutionRequest] = []
    adapter.register_target(
        "connector:erp-orders",
        lambda request: _delivered(observed, request),
        allowed_kinds=frozenset({"webhook"}),
    )

    result = adapter.execute(_request("connector:erp-orders"))

    assert result.outcome == "delivered"
    assert observed[0].idempotency_key == "action-effect:run-1:erp"
    with pytest.raises(ActionEffectPermanentError, match="not registered"):
        adapter.execute(_request("https://caller-controlled.example"))


def _request(target_ref: str) -> ActionEffectExecutionRequest:
    effect = compile_action_effects(
        {
            "effects": [
                {
                    "effectId": "erp",
                    "kind": "webhook",
                    "phase": "before_commit",
                    "targetRef": target_ref,
                }
            ]
        }
    )[0]
    return ActionEffectExecutionRequest(
        tenant_id="tenant-a",
        action_run_id="run-1",
        actor_user_id="user-1",
        request_id="req-1",
        idempotency_key="action-effect:run-1:erp",
        effect=effect,
        parameters={"status": "APPROVED"},
    )


def _delivered(
    observed: list[ActionEffectExecutionRequest], request: ActionEffectExecutionRequest
) -> ActionEffectExecutionResult:
    observed.append(request)
    return ActionEffectExecutionResult(
        outcome="delivered",
        external_execution_id="provider-1",
        response={"statusCode": 202},
        network_evidence={"networkPolicy": "egress-prod"},
    )
