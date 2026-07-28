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
from foundry_lite.application.ports.ai_run_repository import (
    AiExecutionRunRecord,
    AiSessionRecord,
)
from foundry_lite.application.ports.language_model import (
    LanguageModelError,
    ModelMediaReference,
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
from foundry_lite.infrastructure.repositories import SqlAlchemyAiRunRepository, SqlAlchemyModelRegistryRepository
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine

_TENANT = "tenant-demo"
_CTX = RequestContext(tenant_id=_TENANT)


class _SpyLanguageModel:
    profile_name = "spy-language-model"

    def __init__(self) -> None:
        self.calls = 0
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        self.requests.append(request)
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


class _FailingLanguageModel:
    profile_name = "failing-language-model"

    def complete(self, request: ModelRequest) -> ModelResponse:
        raise LanguageModelError("language_model_unavailable")

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

    def get_secret(self, name: str, *, version: str | None = None) -> SecretValue:
        if version is not None:
            raise RuntimeError(f"unexpected secret version: {version}")
        self.requested_names.append(name)
        return SecretValue(name=name, version="v9", value="super-secret-token")


def _engine_with_model(
    *,
    lifecycle: ModelLifecycle = "stable",
    version: str | None = None,
    allowed: tuple[str, ...] = ("public", "internal"),
    region: str = "us-east-1",
    alias_status: AliasStatus = "enabled",
    provider_type: str = "local-fake",
    provider_profile: str = "fake-language-model",
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
                provider_type=provider_type,
                profile_name=provider_profile,
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
        ai_run_repository=SqlAlchemyAiRunRepository(engine),
    )


def _request(*, classification: str = "public", region: str | None = None, environment: str = "prod") -> ModelRequest:
    return ModelRequest(
        model_alias="default-completion",
        messages=(ModelMessage(role="user", content="hello world"),),
        environment=environment,
        data_classification=classification,
        region_requirement=region,
    )


def _engine_with_two_environment_aliases() -> Engine:
    engine = create_engine("sqlite:///:memory:", future=True)
    db.metadata.create_all(engine)
    repository = SqlAlchemyModelRegistryRepository(engine)
    with engine.begin() as conn:
        repository.create_provider(
            transaction=conn,
            record=ModelProviderRecord(
                provider_id="prov-1",
                tenant_scope=_TENANT,
                provider_type="local-fake",
                profile_name="fake-language-model",
                region="us-east-1",
                secret_ref="foundry_aip_token",
                retention_policy="zero_retention",
                training_policy="no_train",
                status="active",
                created_at="2026-06-25T00:00:00Z",
            ),
        )
        for model_id, revision in (("model-prod", "2026-06-01"), ("model-staging", "2026-09-01")):
            repository.create_model(
                transaction=conn,
                record=ModelRecord(
                    model_id=model_id,
                    tenant_scope=_TENANT,
                    provider_id="prov-1",
                    provider_model_id=f"acme-{model_id}",
                    revision=revision,
                    lifecycle="stable",
                    capabilities_json={"streaming": True},
                    context_limit=8192,
                    output_limit=1024,
                    pricing_json={"input_per_1k": 0.5},
                    allowed_classifications=("public", "internal"),
                    created_at="2026-06-25T00:00:00Z",
                ),
            )
        for environment, model_id in (("prod", "model-prod"), ("staging", "model-staging")):
            repository.create_alias(
                transaction=conn,
                record=ModelAliasRecord(
                    alias="default-completion",
                    tenant_scope=_TENANT,
                    environment=environment,
                    model_id=model_id,
                    version=None,
                    status="enabled",
                    eval_run_id=None,
                    effective_at=None,
                    retired_at=None,
                ),
            )
    return engine


def test_gateway_resolves_pinned_revision() -> None:
    engine = _engine_with_model(version="2026-05-15")
    response = _gateway(engine, FakeLanguageModel()).invoke(_CTX, _request())

    assert response.provider == "local-fake"
    assert response.resolved_model_id == "model-1"
    assert response.resolved_model_revision == "2026-05-15"


def test_gateway_resolves_floating_latest_when_unpinned() -> None:
    engine = _engine_with_model(version=None)
    response = _gateway(engine, FakeLanguageModel()).invoke(_CTX, _request())

    assert response.resolved_model_revision == "2026-06-01"


@pytest.mark.parametrize(
    ("expected_model_id", "expected_model_revision"),
    (("model-older", "2026-06-01"), ("model-1", "2026-05-01")),
)
def test_gateway_rejects_promoted_model_pin_drift_before_adapter_call(
    expected_model_id: str,
    expected_model_revision: str,
) -> None:
    engine = _engine_with_model()
    adapter = _SpyLanguageModel()
    request = ModelRequest(
        model_alias="default-completion",
        expected_model_id=expected_model_id,
        expected_model_revision=expected_model_revision,
        messages=(ModelMessage(role="user", content="must not escape"),),
        data_classification="public",
    )

    with pytest.raises(AdapterError) as excinfo:
        _gateway(engine, adapter).invoke(_CTX, request)

    assert adapter.calls == 0
    assert excinfo.value.failure.details["reason"] == "model_resolution_pin_mismatch"


def test_gateway_fake_path_returns_tokens_and_hashes() -> None:
    engine = _engine_with_model()
    response = _gateway(engine, FakeLanguageModel()).invoke(_CTX, _request())

    assert response.content == "echo: hello world"
    assert response.input_tokens == 2
    assert response.output_tokens == 3
    assert response.prompt_hash.startswith("sha256:")  # sha256 over messages
    assert response.model_hash.startswith("sha256:")


def test_gateway_attaches_resolved_provider_route_before_adapter_call() -> None:
    engine = _engine_with_model(version="2026-05-15")
    adapter = _SpyLanguageModel()

    _gateway(engine, adapter).invoke(_CTX, _request())

    route = adapter.requests[0].resolved_route
    assert route is not None
    assert route.tenant_id == _TENANT
    assert route.provider_type == "local-fake"
    assert route.provider_profile == "fake-language-model"
    assert route.provider_model_id == "acme-large-v2"
    assert route.catalog_model_id == "model-1"
    assert route.model_revision == "2026-05-15"
    assert route.secret_ref == "foundry_aip_token"


def test_gateway_blocks_provider_adapter_mismatch_before_rebranding_fake_response() -> None:
    engine = _engine_with_model(provider_type="anthropic", provider_profile="anthropic")

    with pytest.raises(AdapterError) as excinfo:
        _gateway(engine, FakeLanguageModel()).invoke(_CTX, _request())

    assert excinfo.value.failure.kind == "unsupported"
    assert excinfo.value.failure.details["reason"] == "provider_adapter_mismatch"


def test_gateway_prompt_hash_pins_media_reference_identity() -> None:
    engine = _engine_with_model()
    gateway = _gateway(engine, FakeLanguageModel())
    first = gateway.invoke(_CTX, _media_request("media-v1"))
    second = gateway.invoke(_CTX, _media_request("media-v2"))

    assert first.prompt_hash != second.prompt_hash


def _media_request(media_item_version_id: str) -> ModelRequest:
    return ModelRequest(
        model_alias="default-completion",
        messages=(
            ModelMessage(
                role="user",
                content="Inspect the attached image",
                media_references=(
                    ModelMediaReference(
                        media_item_version_id=media_item_version_id,
                        mime_type="image/jpeg",
                        content_hash=f"sha256:{media_item_version_id}",
                    ),
                ),
            ),
        ),
        data_classification="public",
    )


def test_gateway_records_model_call_for_seeded_ai_run() -> None:
    engine = _engine_with_model()
    _seed_ai_run(engine, ai_run_id="ai-run-gateway-ledger")

    response = _gateway(engine, FakeLanguageModel()).invoke(
        _CTX,
        ModelRequest(
            model_alias="default-completion",
            messages=(ModelMessage(role="user", content="hello ledger"),),
            ai_run_id="ai-run-gateway-ledger",
            request_hash="sha256:compiled-prompt",
            model_call_attempt=1,
            data_classification="internal",
        ),
    )

    with engine.begin() as conn:
        row = conn.execute(select(db.ai_model_calls)).mappings().one()

    assert response.content == "echo: hello ledger"
    assert row["ai_run_id"] == "ai-run-gateway-ledger"
    assert row["attempt"] == 1
    assert row["provider"] == "local-fake"
    assert row["model_revision"] == "2026-06-01"
    assert row["request_hash"] == "sha256:compiled-prompt"
    assert str(row["response_hash"]).startswith("sha256:")
    assert row["status"] == "succeeded"
    assert row["error_json"] is None


def test_gateway_requires_seeded_ai_run_before_network_call() -> None:
    engine = _engine_with_model()
    spy = _SpyLanguageModel()

    with pytest.raises(AdapterError) as excinfo:
        _gateway(engine, spy).invoke(
            _CTX,
            ModelRequest(
                model_alias="default-completion",
                messages=(ModelMessage(role="user", content="must not escape"),),
                ai_run_id="ai-run-missing",
                request_hash="sha256:missing-run",
                data_classification="internal",
            ),
        )

    assert spy.calls == 0
    assert excinfo.value.failure.details["reason"] == "ai_run_ledger_not_seeded"
    with engine.begin() as conn:
        assert conn.execute(select(db.ai_model_calls)).all() == []


def test_gateway_records_failed_provider_attempt_without_raw_prompt() -> None:
    engine = _engine_with_model()
    _seed_ai_run(engine, ai_run_id="ai-run-provider-failed")

    with pytest.raises(LanguageModelError):
        _gateway(engine, _FailingLanguageModel()).invoke(
            _CTX,
            ModelRequest(
                model_alias="default-completion",
                messages=(ModelMessage(role="user", content="secret prompt text"),),
                ai_run_id="ai-run-provider-failed",
                request_hash="sha256:provider-failure",
                data_classification="internal",
            ),
        )

    with engine.begin() as conn:
        row = conn.execute(select(db.ai_model_calls)).mappings().one()

    assert row["ai_run_id"] == "ai-run-provider-failed"
    assert row["status"] == "failed"
    assert row["request_hash"] == "sha256:provider-failure"
    assert str(row["response_hash"]).startswith("sha256:")
    assert row["input_tokens"] == 0
    assert row["output_tokens"] == 0
    assert row["error_json"] == {"reason": "language_model_unavailable"}
    assert "secret prompt text" not in str(row)


def test_gateway_denies_egress_when_classification_exceeds_allowance() -> None:
    engine = _engine_with_model(allowed=("public",))
    spy = _SpyLanguageModel()
    gateway = _gateway(engine, spy)

    with pytest.raises(AdapterError) as excinfo:
        gateway.invoke(_CTX, _request(classification="restricted"))

    assert spy.calls == 0  # denied BEFORE the adapter is called
    assert excinfo.value.failure.kind == "authorization"
    assert excinfo.value.failure.details["reason"] == "egress_denied"


def test_gateway_denies_egress_when_classification_is_unset() -> None:
    # Fail CLOSED: an empty/unset classification must never bypass the export-control gate, even when
    # the model has allowances — an unmarked request is denied rather than silently egressed.
    engine = _engine_with_model(allowed=("public", "internal"))
    spy = _SpyLanguageModel()
    gateway = _gateway(engine, spy)

    with pytest.raises(AdapterError) as excinfo:
        gateway.invoke(_CTX, _request(classification=""))

    assert spy.calls == 0  # denied BEFORE the adapter is called
    assert excinfo.value.failure.kind == "authorization"
    assert excinfo.value.failure.details["reason"] == "egress_denied"


def test_gateway_resolves_environment_scoped_alias_deterministically() -> None:
    # The same alias exists in prod and staging pointing at DIFFERENT models; prod must resolve to the
    # prod model deterministically (never nondeterministically via ``aliases[0]``).
    engine = _engine_with_two_environment_aliases()

    prod = _gateway(engine, FakeLanguageModel()).invoke(_CTX, _request())
    staging = _gateway(engine, FakeLanguageModel()).invoke(_CTX, _request(environment="staging"))

    assert prod.resolved_model_id == "model-prod"
    assert prod.resolved_model_revision == "2026-06-01"
    assert staging.resolved_model_id == "model-staging"
    assert staging.resolved_model_revision == "2026-09-01"


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


def _seed_ai_run(engine: Engine, *, ai_run_id: str) -> None:
    repository = SqlAlchemyAiRunRepository(engine)
    with engine.begin() as conn:
        repository.create_session(
            transaction=conn,
            record=AiSessionRecord(
                id=f"{ai_run_id}-session",
                tenant_id=_TENANT,
                agent_version_id="agent.gateway-ledger.v1",
                actor_user_id="ops-user",
                status="active",
                created_at="2026-06-26T00:00:00Z",
                last_activity_at="2026-06-26T00:00:00Z",
            ),
        )
        repository.create_execution_run(
            transaction=conn,
            record=AiExecutionRunRecord(
                id=ai_run_id,
                tenant_id=_TENANT,
                session_id=f"{ai_run_id}-session",
                agent_version_id="agent.gateway-ledger.v1",
                actor_user_id="ops-user",
                request_id="req-gateway-ledger",
                trace_id=f"{ai_run_id}-trace",
                status="running",
                ontology_version_id="active-ontology",
                model_alias_version="default-completion",
                resolved_model_id="model-1",
                resolved_model_revision="2026-06-01",
                prompt_version_id="prompt-gateway-ledger@v1",
                compiled_prompt_hash="sha256:compiled-prompt",
                tool_manifest_hash="sha256:tool-manifest",
                context_manifest_hash="sha256:context-manifest",
                state_snapshot_hash="sha256:state-snapshot",
                policy_snapshot_hash="sha256:policy-snapshot",
                budget_json={"maxModelCalls": 1},
                usage_json=None,
                error_json=None,
                started_at="2026-06-26T00:00:00Z",
                completed_at=None,
            ),
        )
