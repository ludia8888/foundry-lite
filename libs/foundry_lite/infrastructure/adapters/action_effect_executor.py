"""Allowlisted Action effect adapter used by local development and contract tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from urllib.parse import urljoin

from sqlalchemy.engine import Engine

from foundry_lite.application.ports.action_effect_executor import (
    ActionEffectExecutionRequest,
    ActionEffectExecutionResult,
    ActionEffectPermanentError,
    ActionEffectTransientError,
)
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.connector_adapter import RestAuthConfig
from foundry_lite.application.ports.connector_registry_repository import ConnectorRegistryRepository
from foundry_lite.application.ports.secret_provider import SecretProvider
from foundry_lite.application.ports.stream_adapter import StreamAdapter, StreamPublishRequest
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters.rest_connector import (
    _auth_headers,
    secure_http_json_write,
)

ActionEffectHandler = Callable[[ActionEffectExecutionRequest], ActionEffectExecutionResult]


class AllowlistedActionEffectExecutor:
    """In-process contract adapter with an explicit target allowlist."""

    """Dispatch only to operator-registered target references, never caller URIs."""

    profile_name = "allowlisted-action-effects"

    def __init__(self) -> None:
        self._targets: dict[str, tuple[frozenset[str], ActionEffectHandler]] = {}

    def register_target(self, target_ref: str, handler: ActionEffectHandler, *, allowed_kinds: frozenset[str]) -> None:
        """Register one test/development target and its accepted effect kinds."""
        if not target_ref.strip() or not allowed_kinds:
            raise ValueError("Action effect target registration requires a reference and allowed kinds")
        self._targets[target_ref] = (allowed_kinds, handler)

    def execute(self, request: ActionEffectExecutionRequest) -> ActionEffectExecutionResult:
        """Execute only a previously registered target and kind."""
        registered = self._targets.get(request.effect.target_ref)
        if registered is None:
            raise ActionEffectPermanentError(f"Action effect target is not registered: {request.effect.target_ref}")
        allowed_kinds, handler = registered
        if request.effect.kind not in allowed_kinds:
            raise ActionEffectPermanentError(f"Action effect kind is not allowed for target: {request.effect.kind}")
        return handler(request)


class ConnectorActionEffectExecutor:
    """Production adapter using registered connectors, secrets, and network policy."""

    """Resolve effects through registered connectors or governed stream targets."""

    profile_name = "registered-connector-action-effects"

    def __init__(
        self,
        engine: Engine,
        connector_repository: ConnectorRegistryRepository,
        secret_provider: SecretProvider,
        stream_adapter: StreamAdapter,
    ) -> None:
        self._engine = engine
        self._connector_repository = connector_repository
        self._secret_provider = secret_provider
        self._stream_adapter = stream_adapter

    def execute(self, request: ActionEffectExecutionRequest) -> ActionEffectExecutionResult:
        """Route an effect to a governed connector or stream target."""
        if request.effect.kind in {"webhook", "connector_command"}:
            return self._execute_connector(request)
        return self._execute_stream(request)

    def _execute_connector(self, request: ActionEffectExecutionRequest) -> ActionEffectExecutionResult:
        connector_name, resource_name = _connector_target(request.effect.target_ref)
        with self._engine.begin() as transaction:
            connection = self._connector_repository.connection_by_name(
                transaction=transaction,
                tenant_id=request.tenant_id,
                connector_name=connector_name,
            )
            resource = self._connector_repository.resource_by_name(
                transaction=transaction,
                tenant_id=request.tenant_id,
                connector_name=connector_name,
                resource_name=resource_name,
            )
        if connection is None or resource is None or connection["status"] != "active":
            raise ActionEffectPermanentError("Action effect connector target is unavailable")
        headers = _auth_headers(_rest_auth(connection["auth"]), self._secret_provider)
        headers["Idempotency-Key"] = request.idempotency_key
        try:
            result = secure_http_json_write(
                urljoin(connection["base_url"].rstrip("/") + "/", resource["resource_path"].lstrip("/")),
                headers,
                _effect_body(request),
                allow_private_network=connection["allow_private_network"],
                connection_id=f"{request.action_run_id}:{request.effect.effect_id}",
            )
        except AdapterError as exc:
            if exc.failure.is_retryable:
                raise ActionEffectTransientError(exc.message) from exc
            raise ActionEffectPermanentError(exc.message) from exc
        except ValidationFailed as exc:
            raise ActionEffectPermanentError(exc.message) from exc
        return ActionEffectExecutionResult(
            outcome="delivered" if result.outcome == "delivered" else "ambiguous",
            external_execution_id=_external_id(result.response),
            response=result.response,
            network_evidence={
                **dict(result.network_evidence),
                "connectorName": connector_name,
                "resourceName": resource_name,
                "configFingerprint": connection["config_fingerprint"],
            },
        )

    def _execute_stream(self, request: ActionEffectExecutionRequest) -> ActionEffectExecutionResult:
        _require_stream_target(request.effect.kind, request.effect.target_ref)
        try:
            event = self._stream_adapter.publish_event(
                StreamPublishRequest(
                    stream_name=request.effect.target_ref,
                    event_type=f"action.effect.{request.effect.kind}",
                    tenant_id=request.tenant_id,
                    request_id=request.request_id,
                    key=request.idempotency_key,
                    payload=_effect_body(request),
                )
            )
        except AdapterError as exc:
            if exc.failure.is_retryable:
                raise ActionEffectTransientError(exc.message) from exc
            raise ActionEffectPermanentError(exc.message) from exc
        return ActionEffectExecutionResult(
            outcome="delivered",
            external_execution_id=f"{event.stream_name}:{event.offset}",
            response={"streamName": event.stream_name, "offset": event.offset},
            network_evidence={"adapterProfile": self._stream_adapter.profile_name},
        )


def _connector_target(target_ref: str) -> tuple[str, str]:
    if not target_ref.startswith("connector:"):
        raise ActionEffectPermanentError("webhook Action effect must use connector:<name>/<resource>")
    path = target_ref.removeprefix("connector:").split("/", 1)
    if len(path) != 2 or not all(item.strip() for item in path):
        raise ActionEffectPermanentError("webhook Action effect target must include a registered resource")
    return path[0], path[1]


def _rest_auth(auth: Mapping[str, object]) -> RestAuthConfig:
    mode = str(auth.get("mode") or "none")
    if mode == "bearer":
        return RestAuthConfig(mode="bearer", token_secret_ref=_required(auth, "tokenSecretRef"))
    if mode == "basic":
        return RestAuthConfig(mode="basic", basic_credentials_secret_ref=_required(auth, "basicCredentialsSecretRef"))
    if mode == "header":
        return RestAuthConfig(
            mode="header",
            header_name=_required(auth, "headerName"),
            header_value_secret_ref=_required(auth, "headerValueSecretRef"),
        )
    if mode != "none":
        raise ActionEffectPermanentError("Action effect connector auth mode is unsupported")
    return RestAuthConfig()


def _required(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ActionEffectPermanentError("Action effect connector auth is incomplete")
    return item


def _require_stream_target(kind: str, target_ref: str) -> None:
    prefixes = {
        "event": "topic:",
        "notification": "notification-policy:",
        "schedule_build": "schedule:",
    }
    prefix = prefixes.get(kind)
    if prefix is None or not target_ref.startswith(prefix):
        raise ActionEffectPermanentError("Action effect stream target is not allowlisted for its kind")


def _effect_body(request: ActionEffectExecutionRequest) -> dict[str, object]:
    return {
        "actionRunId": request.action_run_id,
        "effectId": request.effect.effect_id,
        "idempotencyKey": request.idempotency_key,
        "payload": dict(request.effect.payload),
        "committedResult": dict(request.committed_result or {}),
    }


def _external_id(response: Mapping[str, object]) -> str | None:
    body = response.get("body")
    if isinstance(body, Mapping):
        value = body.get("id") or body.get("executionId")
        if isinstance(value, str) and value:
            return value
    return None
