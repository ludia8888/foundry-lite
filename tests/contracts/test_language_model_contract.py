"""Contract for ``LanguageModelAdapter`` (AIP-lite P0b governed Model Gateway seam, §8.1/§15.1).

Locks the Protocol shape: ``profile_name``, ``complete(ModelRequest) -> ModelResponse`` returning
content + token usage + finish_reason + ``normalized_tool_calls``, a ``stream(ModelRequest) ->
Iterable[ModelEvent]`` that yields the same content, and a typed ``failure_contract``. The
deterministic ``FakeLanguageModel`` stands in for the DEFERRED provider-compatible proxy so app
logic never imports a provider SDK.
"""

from __future__ import annotations

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.language_model import (
    LanguageModelAdapter,
    ModelEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
)
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


def test_language_model_declares_failure_contract() -> None:
    adapter: LanguageModelAdapter = FakeLanguageModel()
    contract = adapter.failure_contract()

    assert isinstance(contract, AdapterFailureContract)
    assert adapter.profile_name == "fake-language-model"
    assert contract.adapter_profile == "fake-language-model"
