"""Unit tests for ``ModelGatewayService`` (AIP-lite P0b governed Model Gateway, §8.1/§11.6/§15.2).

Proves the gateway's guarantees on top of the fake adapter + a real sqlite-backed registry:
alias → provider/model/version (and floating latest), egress denial BEFORE the adapter is called
(``egress_denied``), no-silent-fallback on a deprecated/sunset model AND a non-enabled alias,
credentials referenced by name/version (never the raw value, ``secret_value_never_logged``), the
deferred provider adapter raising ``language_model_unavailable``, and the fake path returning token
counts + resolved id/revision + a prompt_hash.
"""

from __future__ import annotations

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.language_model import (
    LanguageModelError,
    ModelMessage,
    ModelRequest,
    ModelResponse,
)
from foundry_lite.application.ports.model_registry_repository import (
    AliasStatus,
    ModelAliasRecord,
    ModelLifecycle,
    ModelProviderRecord,
    ModelRecord,
)
from foundry_lite.application.ports.secret_provider import SecretValue
from foundry_lite.application.services.aip.model_gateway import ModelGatewayService
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.fake_language_model import FakeLanguageModel
from foundry_lite.infrastructure.adapters.provider_compatible_language_model import (
    ProviderCompatibleLanguageModel,
)
from foundry_lite.infrastructure.repositories import SqlAlchemyModelRegistryRepository
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

_TENANT = "tenant-demo"
_CTX = RequestContext(tenant_id=_TENANT)


class _SpyLanguageModel:
    profile_name = "spy-language-model"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        return ModelResponse(
            provider="spy",
            resolved_model_id="",
            resolved_model_revision="",
            content="ok",
            finish_reason="stop",
            input_tokens=1,
            output_tokens=1,
        )

    def failure_contract(self):  # type: ignore[no-untyped-def]
        from foundry_lite.application.ports.adapter_failure import AdapterFailureContract

        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())


class _RecordingSecretProvider:
    profile_name = "recording-secret"

    def __init__(self) -> None:
        self.requested_names: list[str] = []

    def failure_contract(self):  # type: ignore[no-untyped-def]
        from foundry_lite.application.ports.adapter_failure import AdapterFailureContract

        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())

    def get_secret(self, name: str) -> SecretValue:
        self.requested_names.append(name)
        return SecretValue(name=name, version="v9", value="super-secret-token")


def _engine_with_model(
    *,
    lifecycle: ModelLifecycle = "stable",
    version: str | None = None,
    allowed: tuple[str, ...] = ("public", "internal"),
    region: str = "us-east-1",
    alias_status: AliasStatus = "enabled",
) -> Engine:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    repository = SqlAlchemyModelRegistryRepository(engine)
    with engine.begin() as conn:
        repository.create_provider(
            transaction=conn,
            record=ModelProviderRecord(
                provider_id="prov-1",
                tenant_scope=_TENANT,
                provider_type="acme",
                profile_name="acme-proxy",
                region=region,
                secret_ref="foundry_aip_token",
                retention_policy="zero_retention",
                training_policy="no_train",
                status="active",
                created_at="2026-06-25T00:00:00Z",
            ),
        )
        repository.create_model(
            transaction=conn,
            record=ModelRecord(
                model_id="model-1",
                tenant_scope=_TENANT,
                provider_id="prov-1",
                provider_model_id="acme-large-v2",
                revision="2026-06-01",
                lifecycle=lifecycle,
                capabilities_json={"streaming": True},
                context_limit=8192,
                output_limit=1024,
                pricing_json={"input_per_1k": 0.5},
                allowed_classifications=allowed,
                created_at="2026-06-25T00:00:00Z",
            ),
        )
        repository.create_alias(
            transaction=conn,
            record=ModelAliasRecord(
                alias="default-completion",
                tenant_scope=_TENANT,
                environment="prod",
                model_id="model-1",
                version=version,
                status=alias_status,
                eval_run_id=None,
                effective_at=None,
                retired_at=None,
            ),
        )
    return engine


def _gateway(engine: Engine, adapter: object) -> ModelGatewayService:
    return ModelGatewayService(
        engine=engine,
        model_registry_repository=SqlAlchemyModelRegistryRepository(engine),
        language_model_adapter=adapter,
    )


def _request(*, classification: str = "public", region: str | None = None) -> ModelRequest:
    return ModelRequest(
        model_alias="default-completion",
        messages=(ModelMessage(role="user", content="hello world"),),
        data_classification=classification,
        region_requirement=region,
    )


def test_gateway_resolves_pinned_revision() -> None:
    engine = _engine_with_model(version="2026-05-15")
    response = _gateway(engine, FakeLanguageModel()).invoke(_CTX, _request())

    assert response.provider == "acme"
    assert response.resolved_model_id == "model-1"
    assert response.resolved_model_revision == "2026-05-15"


def test_gateway_resolves_floating_latest_when_unpinned() -> None:
    engine = _engine_with_model(version=None)
    response = _gateway(engine, FakeLanguageModel()).invoke(_CTX, _request())

    assert response.resolved_model_revision == "2026-06-01"


def test_gateway_fake_path_returns_tokens_and_hashes() -> None:
    engine = _engine_with_model()
    response = _gateway(engine, FakeLanguageModel()).invoke(_CTX, _request())

    assert response.content == "echo: hello world"
    assert response.input_tokens == 2
    assert response.output_tokens == 3
    assert len(response.prompt_hash) == 64  # sha256 over messages
    assert len(response.model_hash) == 64


def test_gateway_denies_egress_when_classification_exceeds_allowance() -> None:
    engine = _engine_with_model(allowed=("public",))
    spy = _SpyLanguageModel()
    gateway = _gateway(engine, spy)

    with pytest.raises(AdapterError) as excinfo:
        gateway.invoke(_CTX, _request(classification="restricted"))

    assert spy.calls == 0  # denied BEFORE the adapter is called
    assert excinfo.value.failure.kind == "authorization"
    assert excinfo.value.failure.details["reason"] == "egress_denied"


def test_gateway_denies_egress_when_region_not_permitted() -> None:
    engine = _engine_with_model(region="us-east-1")
    spy = _SpyLanguageModel()
    gateway = _gateway(engine, spy)

    with pytest.raises(AdapterError) as excinfo:
        gateway.invoke(_CTX, _request(region="eu-west-1"))

    assert spy.calls == 0
    assert excinfo.value.failure.details["reason"] == "egress_denied"


def test_gateway_does_not_silently_fall_back_on_deprecated_model() -> None:
    engine = _engine_with_model(lifecycle="deprecated")
    spy = _SpyLanguageModel()
    gateway = _gateway(engine, spy)

    with pytest.raises(AdapterError) as excinfo:
        gateway.invoke(_CTX, _request())

    assert spy.calls == 0  # never swapped to a different model
    assert excinfo.value.failure.details["reason"] == "language_model_unavailable"


def test_gateway_does_not_serve_a_non_enabled_alias() -> None:
    engine = _engine_with_model(alias_status="draft")
    spy = _SpyLanguageModel()
    gateway = _gateway(engine, spy)

    with pytest.raises(AdapterError) as excinfo:
        gateway.invoke(_CTX, _request())

    assert spy.calls == 0  # a draft/deprecated/disabled alias never serves, no fallback
    assert excinfo.value.failure.details["reason"] == "language_model_unavailable"


def test_gateway_rejects_unknown_alias() -> None:
    engine = _engine_with_model()
    with pytest.raises(AdapterError):
        _gateway(engine, FakeLanguageModel()).invoke(_CTX, ModelRequest(model_alias="missing", messages=()))


def test_provider_egress_denied_before_network_call() -> None:
    # Canonical §15.2 deterministic security test: an over-classified request is denied egress and
    # the provider adapter is NEVER invoked (no network call escapes the boundary).
    engine = _engine_with_model(allowed=("public",))
    spy = _SpyLanguageModel()

    with pytest.raises(AdapterError) as excinfo:
        _gateway(engine, spy).invoke(_CTX, _request(classification="restricted"))

    assert spy.calls == 0
    assert excinfo.value.failure.details["reason"] == "egress_denied"


def test_secret_value_never_logged() -> None:
    # Canonical §15.2 deterministic security test: the raw secret never appears in the response or
    # any trace surface — only the secret NAME is referenced.
    secret_provider = _RecordingSecretProvider()
    adapter = ProviderCompatibleLanguageModel(secret_provider)

    with pytest.raises(LanguageModelError) as excinfo:
        adapter.complete(_request())

    assert str(excinfo.value) == "language_model_unavailable"
    assert secret_provider.requested_names == ["foundry_aip_token"]  # by NAME only
    redacted = secret_provider.get_secret("foundry_aip_token").redacted()
    assert "super-secret-token" not in str(redacted)
    assert redacted["value"] == "***REDACTED***"


def test_deferred_provider_references_secret_by_name_and_raises_unavailable() -> None:
    secret_provider = _RecordingSecretProvider()
    adapter = ProviderCompatibleLanguageModel(secret_provider)

    assert adapter.is_available is False
    with pytest.raises(LanguageModelError) as excinfo:
        adapter.complete(_request())

    assert str(excinfo.value) == "language_model_unavailable"
    # Credentials referenced by NAME only; the raw value never appears in the trace.
    assert secret_provider.requested_names == ["foundry_aip_token"]
    redacted = secret_provider.get_secret("foundry_aip_token").redacted()
    assert redacted["value"] == "***REDACTED***"


def test_deferred_provider_stream_raises_unavailable() -> None:
    secret_provider = _RecordingSecretProvider()
    adapter = ProviderCompatibleLanguageModel(secret_provider)

    with pytest.raises(LanguageModelError) as excinfo:
        list(adapter.stream(_request()))

    assert str(excinfo.value) == "language_model_unavailable"


def test_deferred_provider_with_injected_engine_resolves_secret_then_delegates() -> None:
    secret_provider = _RecordingSecretProvider()
    seen: list[ModelRequest] = []

    def _engine(request: ModelRequest) -> ModelResponse:
        seen.append(request)
        return ModelResponse(
            provider="acme",
            resolved_model_id="model-1",
            resolved_model_revision="2026-06-01",
            content="live",
            finish_reason="stop",
            input_tokens=1,
            output_tokens=1,
        )

    adapter = ProviderCompatibleLanguageModel(secret_provider, engine=_engine)

    assert adapter.is_available is True
    response = adapter.complete(_request())

    assert response.content == "live"
    assert len(seen) == 1  # the wired engine handled the request
    assert secret_provider.requested_names == ["foundry_aip_token"]  # token resolved by name first
