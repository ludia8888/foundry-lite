from __future__ import annotations

from collections.abc import Mapping

import pytest
from foundry_lite.application.ports.action_effect_executor import (
    ActionEffectExecutionRequest,
    ActionEffectExecutionResult,
    ActionEffectPermanentError,
)
from foundry_lite.domain.action_runtime.action_effects import compile_action_effects
from foundry_lite.infrastructure.adapters import FakeStreamAdapter
from foundry_lite.infrastructure.adapters.action_effect_executor import (
    AllowlistedActionEffectExecutor,
    ConnectorActionEffectExecutor,
)
from foundry_lite.infrastructure.adapters.rest_connector import SecureHttpWriteResult
from foundry_lite.infrastructure.secrets.env import EnvSecretProvider
from sqlalchemy import create_engine


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


@pytest.mark.parametrize(
    ("kind", "target_ref"),
    (
        ("event", "topic:order-approved"),
        ("notification", "notification-policy:operations"),
        ("schedule_build", "schedule:refresh-orders"),
    ),
)
def test_production_effect_executor_routes_each_stream_kind_with_the_stable_key(kind: str, target_ref: str) -> None:
    engine = create_engine("sqlite://", future=True)
    stream = FakeStreamAdapter()
    adapter = ConnectorActionEffectExecutor(engine, _ConnectorRepository(), EnvSecretProvider(environ={}), stream)
    try:
        result = adapter.execute(_after_request(kind, target_ref))
    finally:
        engine.dispose()

    event = stream.read_events(target_ref)[0]
    assert result.outcome == "delivered"
    assert event.key == "action-effect:run-1:after"
    assert event.event_type == f"action.effect.{kind}"
    assert event.payload["effectId"] == "after"


def test_production_effect_executor_routes_connector_commands_through_registered_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from foundry_lite.infrastructure.adapters import action_effect_executor as effect_module

    observed: dict[str, object] = {}

    def deliver(
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        *,
        allow_private_network: bool,
        connection_id: str,
    ) -> SecureHttpWriteResult:
        observed.update(
            url=url,
            idempotency_key=headers["Idempotency-Key"],
            payload=dict(payload),
            allow_private_network=allow_private_network,
            connection_id=connection_id,
        )
        return SecureHttpWriteResult(
            "delivered",
            {"body": {"executionId": "provider-command-1"}},
            {"host": "connector.example"},
        )

    monkeypatch.setattr(effect_module, "secure_http_json_write", deliver)
    engine = create_engine("sqlite://", future=True)
    adapter = ConnectorActionEffectExecutor(
        engine,
        _ConnectorRepository(),
        EnvSecretProvider(environ={}),
        FakeStreamAdapter(),
    )
    try:
        result = adapter.execute(_after_request("connector_command", "connector:booking/commands"))
    finally:
        engine.dispose()

    assert result.external_execution_id == "provider-command-1"
    assert observed["url"] == "https://connector.example/api/commands"
    assert observed["idempotency_key"] == "action-effect:run-1:after"
    assert observed["connection_id"] == "run-1:after"


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


def _after_request(kind: str, target_ref: str) -> ActionEffectExecutionRequest:
    effect = compile_action_effects(
        {
            "effects": [
                {
                    "effectId": "after",
                    "kind": kind,
                    "phase": "after_commit",
                    "targetRef": target_ref,
                    "payload": {"message": "approved"},
                }
            ]
        }
    )[0]
    return ActionEffectExecutionRequest(
        tenant_id="tenant-a",
        action_run_id="run-1",
        actor_user_id="user-1",
        request_id="req-1",
        idempotency_key="action-effect:run-1:after",
        effect=effect,
        parameters={},
        committed_result={"editCount": 1},
    )


class _ConnectorRepository:
    def connection_by_name(self, **_kwargs):
        return {
            "base_url": "https://connector.example/api",
            "auth": {"mode": "none"},
            "allow_private_network": False,
            "status": "active",
            "config_fingerprint": "sha256:connector",
        }

    def resource_by_name(self, **_kwargs):
        return {"resource_path": "commands"}


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
