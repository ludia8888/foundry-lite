from __future__ import annotations

from collections.abc import Mapping

import pytest
from foundry_lite.application.ports.action_effect_executor import (
    ActionEffectExecutionRequest,
    ActionEffectExecutionResult,
    ActionEffectPermanentError,
    require_action_effect_execution_result,
)
from foundry_lite.domain.action_runtime.action_effects import compile_action_effects
from foundry_lite.domain.errors import InvariantViolation
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


@pytest.mark.parametrize(
    "invalid_evidence",
    [
        {"values": {"a", "b"}},
        {"score": float("nan")},
        {1: "non-string-key"},
    ],
)
def test_action_effect_result_requires_durable_json_evidence(invalid_evidence: Mapping[object, object]) -> None:
    with pytest.raises(InvariantViolation, match="invalid evidence"):
        require_action_effect_execution_result(
            ActionEffectExecutionResult("delivered", None, invalid_evidence, {})  # type: ignore[arg-type]
        )


def test_action_effect_result_rejects_circular_evidence() -> None:
    circular: dict[str, object] = {}
    circular["self"] = circular
    with pytest.raises(InvariantViolation, match="invalid evidence"):
        require_action_effect_execution_result(ActionEffectExecutionResult("delivered", None, circular, {}))


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


@pytest.mark.parametrize("resource_path", ["https://evil.example/capture", "//evil.example/capture"])
def test_production_effect_executor_rejects_legacy_resource_paths_that_can_change_origin(
    monkeypatch: pytest.MonkeyPatch,
    resource_path: str,
) -> None:
    from foundry_lite.infrastructure.adapters import action_effect_executor as effect_module

    calls = 0

    def must_not_deliver(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("untrusted resource path reached the transport")

    monkeypatch.setattr(effect_module, "secure_http_json_write", must_not_deliver)
    engine = create_engine("sqlite://", future=True)
    adapter = ConnectorActionEffectExecutor(
        engine,
        _ConnectorRepository(resource_path),
        EnvSecretProvider(environ={}),
        FakeStreamAdapter(),
    )
    try:
        with pytest.raises(ActionEffectPermanentError, match="same registered origin"):
            adapter.execute(_after_request("connector_command", "connector:booking/commands"))
    finally:
        engine.dispose()

    assert calls == 0


def test_production_effect_executor_classifies_missing_auth_secret_before_transport_as_permanent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from foundry_lite.infrastructure.adapters import action_effect_executor as effect_module

    calls = 0

    def must_not_deliver(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("missing connector auth reached the transport")

    monkeypatch.setattr(effect_module, "secure_http_json_write", must_not_deliver)
    engine = create_engine("sqlite://", future=True)
    adapter = ConnectorActionEffectExecutor(
        engine,
        _ConnectorRepository(auth={"mode": "bearer", "tokenSecretRef": "missing-token"}),
        EnvSecretProvider(environ={}),
        FakeStreamAdapter(),
    )
    try:
        with pytest.raises(ActionEffectPermanentError, match="secret is not configured"):
            adapter.execute(_after_request("connector_command", "connector:booking/commands"))
    finally:
        engine.dispose()

    assert calls == 0


def test_production_effect_executor_rejects_legacy_base_url_credentials_before_secret_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from foundry_lite.infrastructure.adapters import action_effect_executor as effect_module

    calls = 0

    def must_not_deliver(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("unsafe legacy base URL reached the transport")

    monkeypatch.setattr(effect_module, "secure_http_json_write", must_not_deliver)
    engine = create_engine("sqlite://", future=True)
    repository = _ConnectorRepository(auth={"mode": "bearer", "tokenSecretRef": "missing-token"})
    repository.base_url = "https://user:password@connector.example/api"
    adapter = ConnectorActionEffectExecutor(
        engine,
        repository,
        EnvSecretProvider(environ={}),
        FakeStreamAdapter(),
    )
    try:
        with pytest.raises(ActionEffectPermanentError, match="base URL"):
            adapter.execute(_after_request("connector_command", "connector:booking/commands"))
    finally:
        engine.dispose()

    assert calls == 0


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
    def __init__(self, resource_path: str = "commands", *, auth: Mapping[str, object] | None = None) -> None:
        self.resource_path = resource_path
        self.auth = auth or {"mode": "none"}
        self.base_url = "https://connector.example/api"

    def connection_by_name(self, **_kwargs):
        return {
            "base_url": self.base_url,
            "auth": self.auth,
            "allow_private_network": False,
            "status": "active",
            "config_fingerprint": "sha256:connector",
        }

    def resource_by_name(self, **_kwargs):
        return {"resource_path": self.resource_path}


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
