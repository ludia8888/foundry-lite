"""Contract for ``LanguageModelAdapter`` (AIP-lite P0b governed Model Gateway seam, §8.1/§15.1).

Locks the Protocol shape: ``profile_name``, ``complete(ModelRequest) -> ModelResponse`` returning
content + token usage + finish_reason + ``normalized_tool_calls``, a ``stream(ModelRequest) ->
Iterable[ModelEvent]`` that yields the same content, and a typed ``failure_contract``. The
deterministic ``FakeLanguageModel`` stands in for the DEFERRED provider-compatible proxy so app
logic never imports a provider SDK.
"""

from __future__ import annotations

import json

from foundry_lite.application.model_gateway_bridge import GovernedSemanticModelBridge
from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.language_model import (
    GovernedSemanticModelPort,
    LanguageModelAdapter,
    ModelEvent,
    ModelMessage,
    ModelRequest,
    ModelResolution,
    ModelResponse,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure.adapters.fake_language_model import FakeLanguageModel


def _request() -> ModelRequest:
    return ModelRequest(
        model_alias="default-completion",
        messages=(ModelMessage(role="user", content="hello world"),),
    )


def test_language_model_complete_returns_governed_response() -> None:
    adapter: LanguageModelAdapter = FakeLanguageModel()
    response = adapter.complete(_request())

    assert isinstance(response, ModelResponse)
    assert response.content == "echo: hello world"
    assert response.finish_reason == "stop"
    assert response.input_tokens == 2
    assert response.output_tokens == 3
    assert response.normalized_tool_calls == ()


def test_language_model_stream_yields_same_content_as_complete() -> None:
    adapter: LanguageModelAdapter = FakeLanguageModel()
    events = list(adapter.stream(_request()))

    assert events
    assert all(isinstance(event, ModelEvent) for event in events)
    assert "".join(event.delta_text for event in events) == adapter.complete(_request()).content
    assert events[-1].finish_reason == "stop"


def test_fake_language_model_returns_schema_requested_citation_claim() -> None:
    adapter: LanguageModelAdapter = FakeLanguageModel()
    request = ModelRequest(
        model_alias="default-completion",
        messages=(
            ModelMessage(
                role="system",
                content='## citation_mapping\n{"citations":[{"context_id":"ctx-order-1"}]}',
            ),
            ModelMessage(role="user", content="hello world"),
        ),
        response_schema=json.dumps({"type": "object", "properties": {"citations": {"type": "array"}}}),
    )

    payload = json.loads(adapter.complete(request).content)

    assert payload["answer"] == "echo: hello world"
    assert payload["citations"][0]["contextId"] == "ctx-order-1"
    assert payload["citations"][0]["citationOrder"] == 1


def test_fake_language_model_falls_back_without_valid_citation_mapping() -> None:
    adapter: LanguageModelAdapter = FakeLanguageModel()
    citing_schema = json.dumps({"type": "object", "properties": {"citations": {"type": "array"}}})

    invalid_schema = ModelRequest(
        model_alias="default-completion",
        messages=(ModelMessage(role="user", content="hello world"),),
        response_schema="{",
    )
    no_mapping = ModelRequest(
        model_alias="default-completion",
        messages=(ModelMessage(role="system", content="## agent_instruction\nAnswer"), _user_message()),
        response_schema=citing_schema,
    )
    malformed_mapping = ModelRequest(
        model_alias="default-completion",
        messages=(ModelMessage(role="system", content="## citation_mapping\n{"), _user_message()),
        response_schema=citing_schema,
    )

    assert adapter.complete(invalid_schema).content == "echo: hello world"
    assert adapter.complete(no_mapping).content == "echo: hello world"
    assert adapter.complete(malformed_mapping).content == "echo: hello world"


def test_fake_language_model_detects_citation_schema_inside_lists() -> None:
    adapter: LanguageModelAdapter = FakeLanguageModel()
    request = ModelRequest(
        model_alias="default-completion",
        messages=(
            ModelMessage(
                role="system",
                content='## citation_mapping\n{"citations":[{"context_id":"ctx-order-2"}]}',
            ),
            _user_message(),
        ),
        response_schema=json.dumps([{"citations": {"type": "array"}}]),
    )

    payload = json.loads(adapter.complete(request).content)

    assert payload["citations"][0]["contextId"] == "ctx-order-2"


def test_language_model_declares_failure_contract() -> None:
    adapter: LanguageModelAdapter = FakeLanguageModel()
    contract = adapter.failure_contract()

    assert isinstance(contract, AdapterFailureContract)
    assert adapter.profile_name == "fake-language-model"
    assert adapter.supported_provider_profiles == ("fake-language-model", "local-fake")
    assert contract.adapter_profile == "fake-language-model"


def test_governed_semantic_bridge_exposes_only_pinned_gateway_invocation() -> None:
    target = _GovernedTarget()
    bridge: GovernedSemanticModelPort = GovernedSemanticModelBridge(target)
    ctx = RequestContext(tenant_id="tenant-contract", request_id="req-contract")

    response = bridge.invoke(ctx, _request())
    resolution = bridge.resolve_model(ctx, "semantic-default")

    assert target.calls == [(ctx, _request())]
    assert target.resolutions == [(ctx, "semantic-default", "prod")]
    assert resolution.model_id == "governed-model"
    assert response.resolved_model_id == "governed-model"
    assert response.resolved_model_revision == "2026-07-16"


class _GovernedTarget:
    def __init__(self) -> None:
        self.calls: list[tuple[RequestContext, ModelRequest]] = []
        self.resolutions: list[tuple[RequestContext, str, str]] = []

    def resolve_model(
        self,
        ctx: RequestContext,
        model_alias: str,
        environment: str = "prod",
    ) -> ModelResolution:
        self.resolutions.append((ctx, model_alias, environment))
        return ModelResolution(
            provider="governed",
            model_id="governed-model",
            revision="2026-07-16",
        )

    def invoke(self, ctx: RequestContext, request: ModelRequest) -> ModelResponse:
        self.calls.append((ctx, request))
        return ModelResponse(
            provider="governed",
            resolved_model_id="governed-model",
            resolved_model_revision="2026-07-16",
            content="{}",
            finish_reason="stop",
            input_tokens=1,
            output_tokens=1,
        )


def _user_message() -> ModelMessage:
    return ModelMessage(role="user", content="hello world")
