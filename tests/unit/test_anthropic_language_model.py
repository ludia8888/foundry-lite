from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import replace

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailureContract
from foundry_lite.application.ports.language_model import (
    ModelInvocationRoute,
    ModelMediaContent,
    ModelMediaReference,
    ModelMessage,
    ModelRequest,
)
from foundry_lite.application.ports.secret_provider import SecretValue
from foundry_lite.infrastructure.adapters.anthropic_language_model import (
    AnthropicHttpResponse,
    AnthropicLanguageModel,
    AnthropicTransportFailure,
)
from foundry_lite.infrastructure.adapters.anthropic_message_codec import (
    build_anthropic_payload,
    parse_anthropic_response,
)

_DUMMY_KEY = "test-anthropic-key-value"


class _SecretProvider:
    profile_name = "test-secret"

    def __init__(self) -> None:
        self.names: list[str] = []

    def get_secret(self, name: str, *, version: str | None = None) -> SecretValue:
        assert version is None
        self.names.append(name)
        return SecretValue(name=name, version="sha256:test", value=_DUMMY_KEY)

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile=self.profile_name, modes=())


class _RotatingSecretProvider(_SecretProvider):
    def __init__(self) -> None:
        super().__init__()
        self.value = "test-key-v1"

    def get_secret(self, name: str, *, version: str | None = None) -> SecretValue:
        assert version is None
        self.names.append(name)
        return SecretValue(name=name, version=f"version:{self.value[-2:]}", value=self.value)


class _MediaResolver:
    def __init__(self) -> None:
        self.references: list[ModelMediaReference] = []

    def read(
        self,
        *,
        tenant_id: str,
        reference: ModelMediaReference,
        expected_classification: str,
    ) -> ModelMediaContent:
        assert tenant_id == "tenant-anthropic"
        assert expected_classification == "public"
        self.references.append(reference)
        content = b"%PDF-1.4 test" if reference.mime_type == "application/pdf" else b"\x89PNG\r\n\x1a\n"
        return ModelMediaContent(
            media_item_version_id=reference.media_item_version_id,
            mime_type=reference.mime_type,
            content_hash=reference.content_hash,
            byte_size=len(content),
            content=content,
        )


def _route(*, model: str = "claude-sonnet-5") -> ModelInvocationRoute:
    return ModelInvocationRoute(
        tenant_id="tenant-anthropic",
        provider_type="anthropic",
        provider_profile="anthropic",
        provider_model_id=model,
        catalog_model_id=f"anthropic:{model}",
        model_revision=model,
        secret_ref="anthropic_api_key",
        capabilities={
            "image_input": True,
            "pdf_input": True,
            "structured_outputs": True,
            "sampling_parameters": False,
        },
        context_limit=1_000_000,
        output_limit=128_000,
    )


def _request(*, messages: tuple[ModelMessage, ...] | None = None) -> ModelRequest:
    return ModelRequest(
        model_alias="document-vlm",
        messages=messages or (ModelMessage(role="user", content="Return one JSON object."),),
        resolved_route=_route(),
        response_schema=json.dumps(
            {
                "type": "object",
                "properties": {"label": {"type": "string"}},
                "required": ["label"],
                "additionalProperties": False,
            }
        ),
        data_classification="public",
        max_output_tokens=128,
        thinking_mode="disabled",
    )


def _success_response(*, headers: Mapping[str, str] | None = None) -> AnthropicHttpResponse:
    return AnthropicHttpResponse(
        status_code=200,
        headers=headers or {"request-id": "req_anthropic_1"},
        body={
            "id": "msg_1",
            "model": "claude-sonnet-5",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": '{"label":"invoice"}'}],
            "usage": {"input_tokens": 17, "output_tokens": 5},
        },
    )


def test_anthropic_compiles_top_level_system_and_structured_output() -> None:
    captured: dict[str, object] = {}

    def transport(headers: Mapping[str, str], payload: Mapping[str, object], timeout: int) -> AnthropicHttpResponse:
        captured.update(headers=dict(headers), payload=dict(payload), timeout=timeout)
        return _success_response()

    secret_provider = _SecretProvider()
    adapter = AnthropicLanguageModel(secret_provider, transport=transport)
    request = _request(
        messages=(
            ModelMessage(role="system", content="Classify the document."),
            ModelMessage(role="user", content="Return the result."),
        )
    )

    response = adapter.complete(request)

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "claude-sonnet-5"
    assert payload["system"] == "Classify the document."
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["messages"] == [{"role": "user", "content": [{"type": "text", "text": "Return the result."}]}]
    assert payload["output_config"] == {
        "format": {"type": "json_schema", "schema": json.loads(request.response_schema or "{}")}
    }
    assert "temperature" not in payload
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["x-api-key"] == _DUMMY_KEY
    assert headers["anthropic-version"] == "2023-06-01"
    assert secret_provider.names == ["anthropic_api_key"]
    assert response.content == '{"label":"invoice"}'
    assert response.provider_request_id == "req_anthropic_1"
    assert response.input_tokens == 17
    assert response.output_tokens == 5


def test_anthropic_normalizes_nested_objects_for_strict_structured_output() -> None:
    captured: dict[str, object] = {}

    def transport(
        _headers: Mapping[str, str],
        payload: Mapping[str, object],
        _timeout: int,
    ) -> AnthropicHttpResponse:
        captured["payload"] = dict(payload)
        return _success_response()

    schema = {
        "type": "object",
        "properties": {
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                },
            }
        },
        "required": ["sections"],
    }
    request = replace(_request(), response_schema=json.dumps(schema))

    AnthropicLanguageModel(_SecretProvider(), transport=transport).complete(request)

    payload = captured["payload"]
    assert isinstance(payload, dict)
    output_config = payload["output_config"]
    assert isinstance(output_config, dict)
    normalized = output_config["format"]["schema"]
    assert normalized["additionalProperties"] is False
    assert normalized["properties"]["sections"]["items"]["additionalProperties"] is False


def test_anthropic_places_pdf_and_image_blocks_before_user_text() -> None:
    resolver = _MediaResolver()
    captured: dict[str, object] = {}

    def transport(_headers: Mapping[str, str], payload: Mapping[str, object], _timeout: int) -> AnthropicHttpResponse:
        captured["payload"] = dict(payload)
        return _success_response()

    references = (
        ModelMediaReference("pdf-v1", "application/pdf", "sha256:pdf"),
        ModelMediaReference("image-v1", "image/png", "sha256:image"),
    )
    request = _request(messages=(ModelMessage(role="user", content="Interpret both.", media_references=references),))

    AnthropicLanguageModel(_SecretProvider(), media_resolver=resolver, transport=transport).complete(request)

    payload = captured["payload"]
    assert isinstance(payload, dict)
    messages = payload["messages"]
    assert isinstance(messages, list)
    content = messages[0]["content"]
    assert content[0]["type"] == "document"
    assert content[0]["source"]["data"] == base64.standard_b64encode(b"%PDF-1.4 test").decode("ascii")
    assert content[1]["type"] == "image"
    assert content[1]["source"]["media_type"] == "image/png"
    assert content[2] == {"type": "text", "text": "Interpret both."}
    assert resolver.references == list(references)


@pytest.mark.parametrize(
    ("status", "expected_kind", "is_retryable"),
    [
        (400, "validation", False),
        (401, "authentication", False),
        (403, "authorization", False),
        (404, "not_found", False),
        (409, "conflict", False),
        (413, "validation", False),
        (429, "rate_limited", True),
        (500, "unavailable", True),
        (502, "unavailable", True),
        (503, "unavailable", True),
        (504, "timeout", True),
        (529, "unavailable", True),
    ],
)
def test_anthropic_maps_http_failures_without_exposing_secret(
    status: int,
    expected_kind: str,
    is_retryable: bool,
) -> None:
    def transport(_headers: Mapping[str, str], _payload: Mapping[str, object], _timeout: int) -> AnthropicHttpResponse:
        return AnthropicHttpResponse(
            status,
            {"request-id": "req_failure"},
            {"error": {"type": "provider_error", "message": f"must not echo {_DUMMY_KEY}"}},
        )

    adapter = AnthropicLanguageModel(_SecretProvider(), transport=transport, max_retries=0)

    with pytest.raises(AdapterError) as excinfo:
        adapter.complete(_request())

    assert excinfo.value.failure.kind == expected_kind
    assert excinfo.value.failure.is_retryable is is_retryable
    assert excinfo.value.failure.details["requestId"] == "req_failure"
    assert _DUMMY_KEY not in str(excinfo.value)
    assert _DUMMY_KEY not in json.dumps(excinfo.value.failure.to_payload())


def test_anthropic_retries_rate_limit_using_retry_after() -> None:
    responses = [
        AnthropicHttpResponse(
            429,
            {"request-id": "req_rate", "retry-after": "0.25"},
            {"error": {"type": "rate_limit_error"}},
        ),
        _success_response(headers={"request-id": "req_after_retry"}),
    ]
    sleeps: list[float] = []

    def transport(_headers: Mapping[str, str], _payload: Mapping[str, object], _timeout: int) -> AnthropicHttpResponse:
        return responses.pop(0)

    response = AnthropicLanguageModel(
        _SecretProvider(),
        transport=transport,
        max_retries=2,
        sleeper=sleeps.append,
    ).complete(_request())

    assert sleeps == [0.25]
    assert response.provider_request_id == "req_after_retry"


def test_anthropic_retry_transport_receives_only_the_remaining_request_budget() -> None:
    now = [100.0]
    observed_timeouts: list[int] = []
    responses = [
        AnthropicHttpResponse(429, {"retry-after": "0.5"}, {"error": {"type": "rate_limit_error"}}),
        _success_response(),
    ]

    def transport(_headers: Mapping[str, str], _payload: Mapping[str, object], timeout: int) -> AnthropicHttpResponse:
        observed_timeouts.append(timeout)
        response = responses.pop(0)
        now[0] += 3.0 if response.status_code == 429 else 0.0
        return response

    def sleep(delay: float) -> None:
        now[0] += delay

    adapter = AnthropicLanguageModel(_SecretProvider(), transport=transport, sleeper=sleep, clock=lambda: now[0])
    adapter.complete(replace(_request(), timeout_seconds=10))

    assert observed_timeouts == [10, 6]


def test_anthropic_re_resolves_secret_reference_on_every_call_for_rotation() -> None:
    secret_provider = _RotatingSecretProvider()
    observed_keys: list[str] = []

    def transport(headers: Mapping[str, str], _payload: Mapping[str, object], _timeout: int) -> AnthropicHttpResponse:
        observed_keys.append(headers["x-api-key"])
        return _success_response()

    adapter = AnthropicLanguageModel(secret_provider, transport=transport)
    adapter.complete(_request())
    secret_provider.value = "test-key-v2"
    adapter.complete(_request())

    assert secret_provider.names == ["anthropic_api_key", "anthropic_api_key"]
    assert observed_keys == ["test-key-v1", "test-key-v2"]


def test_anthropic_transport_timeout_is_typed_and_redacted() -> None:
    def transport(_headers: Mapping[str, str], _payload: Mapping[str, object], _timeout: int) -> AnthropicHttpResponse:
        raise AnthropicTransportFailure("timeout")

    with pytest.raises(AdapterError) as excinfo:
        AnthropicLanguageModel(_SecretProvider(), transport=transport).complete(_request())

    assert excinfo.value.failure.kind == "timeout"
    assert excinfo.value.failure.is_retryable is True
    assert _DUMMY_KEY not in str(excinfo.value)


def test_anthropic_default_transport_rejects_oversized_response_before_json_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OversizedResponse:
        status = 200
        headers: Mapping[str, str] = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, size: int) -> bytes:
            assert size == 17
            return b"x" * size

    monkeypatch.setattr(
        "foundry_lite.infrastructure.adapters.anthropic_language_model._MAX_RESPONSE_BYTES",
        16,
    )
    monkeypatch.setattr(
        "foundry_lite.infrastructure.adapters.anthropic_language_model.urlopen",
        lambda *_args, **_kwargs: OversizedResponse(),
    )

    with pytest.raises(AdapterError) as excinfo:
        AnthropicLanguageModel(_SecretProvider()).complete(_request())

    assert excinfo.value.failure.kind == "validation"
    assert excinfo.value.failure.is_retryable is False
    assert excinfo.value.failure.details["reason"] == "transport_failure"


def test_anthropic_rejects_incomplete_structured_output() -> None:
    def transport(_headers: Mapping[str, str], _payload: Mapping[str, object], _timeout: int) -> AnthropicHttpResponse:
        response = _success_response()
        return AnthropicHttpResponse(
            200,
            response.headers,
            {**response.body, "stop_reason": "max_tokens"},
        )

    with pytest.raises(AdapterError) as excinfo:
        AnthropicLanguageModel(_SecretProvider(), transport=transport).complete(_request())

    assert excinfo.value.failure.details["reason"] == "structured_output_incomplete"
    assert excinfo.value.failure.details["stopReason"] == "max_tokens"
    assert excinfo.value.failure.details["outputTokens"] == 5
    assert excinfo.value.failure.details["providerRequestId"] == "msg_1"


def test_anthropic_provider_default_omits_thinking_configuration() -> None:
    captured: dict[str, object] = {}

    def transport(_headers: Mapping[str, str], payload: Mapping[str, object], _timeout: int) -> AnthropicHttpResponse:
        captured.update(payload)
        return _success_response()

    request = replace(_request(), thinking_mode="provider_default")
    AnthropicLanguageModel(_SecretProvider(), transport=transport).complete(request)

    assert "thinking" not in captured


def test_anthropic_rejects_structured_output_refusal_with_safe_evidence() -> None:
    def transport(_headers: Mapping[str, str], _payload: Mapping[str, object], _timeout: int) -> AnthropicHttpResponse:
        response = _success_response()
        return AnthropicHttpResponse(200, response.headers, {**response.body, "stop_reason": "refusal"})

    with pytest.raises(AdapterError) as excinfo:
        AnthropicLanguageModel(_SecretProvider(), transport=transport).complete(_request())

    assert excinfo.value.failure.details["reason"] == "structured_output_refused"
    assert "invoice" not in str(excinfo.value.failure.details)


def test_anthropic_rejects_provider_model_substitution() -> None:
    def transport(_headers: Mapping[str, str], _payload: Mapping[str, object], _timeout: int) -> AnthropicHttpResponse:
        response = _success_response()
        return AnthropicHttpResponse(200, response.headers, {**response.body, "model": "different-model"})

    with pytest.raises(AdapterError) as excinfo:
        AnthropicLanguageModel(_SecretProvider(), transport=transport).complete(_request())

    assert excinfo.value.failure.kind == "conflict"
    assert excinfo.value.failure.details["reason"] == "provider_model_mismatch"


def test_anthropic_payload_rejects_unsupported_message_contracts() -> None:
    reference = ModelMediaReference("media-v1", "image/png", "sha256:image")
    requests = (
        replace(_request(), tools=("search",)),
        replace(_request(), messages=(ModelMessage(role="system", content="system only"),)),
        replace(
            _request(),
            messages=(ModelMessage(role="system", content="bad", media_references=(reference,)),),
        ),
        replace(
            _request(),
            messages=(ModelMessage(role="assistant", content="bad", media_references=(reference,)),),
        ),
        replace(_request(), messages=(ModelMessage(role="user", content=""),)),
        replace(_request(), resolved_route=None),
        replace(_request(), resolved_route=replace(_route(), provider_profile="other")),
    )

    for request in requests:
        with pytest.raises(AdapterError):
            build_anthropic_payload(request, _MediaResolver())


def test_anthropic_payload_includes_sampling_when_route_allows_it() -> None:
    route = replace(_route(), capabilities={**_route().capabilities, "sampling_parameters": True})
    payload = build_anthropic_payload(
        replace(_request(), resolved_route=route, response_schema=None, temperature=0.4),
        None,
    )

    assert payload["temperature"] == 0.4
    assert "output_config" not in payload


def test_anthropic_media_validation_rejects_missing_resolver_mime_and_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def media_request(mime_type: str) -> ModelRequest:
        reference = ModelMediaReference("media-v1", mime_type, "sha256:media")
        return _request(messages=(ModelMessage(role="user", content="inspect", media_references=(reference,)),))

    with pytest.raises(AdapterError) as raised:
        build_anthropic_payload(media_request("image/png"), None)
    assert raised.value.failure.details["reason"] == "media_resolver_unavailable"

    with pytest.raises(AdapterError) as raised:
        build_anthropic_payload(media_request("video/mp4"), _MediaResolver())
    assert raised.value.failure.details["reason"] == "media_mime_unsupported"

    monkeypatch.setattr(
        "foundry_lite.infrastructure.adapters.anthropic_message_codec._MAX_IMAGE_BASE64_BYTES",
        1,
    )
    with pytest.raises(AdapterError) as raised:
        build_anthropic_payload(media_request("image/png; charset=binary"), _MediaResolver())
    assert raised.value.failure.details["reason"] == "image_too_large"


@pytest.mark.parametrize("schema", ["{", "[]"])
def test_anthropic_rejects_invalid_structured_output_schema(schema: str) -> None:
    with pytest.raises(AdapterError) as raised:
        build_anthropic_payload(replace(_request(), response_schema=schema), None)

    assert raised.value.failure.details["reason"] == "response_schema_invalid"


def test_anthropic_response_parser_normalizes_text_and_tool_calls() -> None:
    request = replace(_request(), response_schema=None)
    response = parse_anthropic_response(
        request,
        {
            "id": "msg-tool",
            "model": "claude-sonnet-5",
            "stop_reason": "tool_use",
            "content": [
                None,
                {"type": "unknown"},
                {"type": "text", "text": "Use "},
                {"type": "tool_use", "name": "lookup", "input": {"id": 7}},
            ],
            "usage": {"input_tokens": True, "output_tokens": "5"},
        },
        {},
        latency_ms=4,
    )

    assert response.content == "Use "
    assert response.normalized_tool_calls[0].tool_name == "lookup"
    assert response.normalized_tool_calls[0].arguments_json == '{"id": 7}'
    assert response.provider_request_id == "msg-tool"
    assert response.input_tokens == response.output_tokens == 0


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({"model": "claude-sonnet-5", "content": "bad"}, "response_content_invalid"),
        ({"model": "claude-sonnet-5", "content": []}, "empty_model_response"),
        (
            {
                "model": "claude-sonnet-5",
                "content": [{"type": "tool_use", "input": {}}],
            },
            "tool_call_invalid",
        ),
    ],
)
def test_anthropic_response_parser_rejects_malformed_content(
    body: dict[str, object],
    reason: str,
) -> None:
    with pytest.raises(AdapterError) as raised:
        parse_anthropic_response(
            replace(_request(), response_schema=None),
            body,
            {},
            latency_ms=1,
        )

    assert raised.value.failure.details["reason"] == reason


def test_anthropic_structured_output_rejects_unexpected_stop_and_oversized_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(AdapterError) as raised:
        parse_anthropic_response(
            _request(),
            {
                "id": "msg-stop",
                "model": "claude-sonnet-5",
                "stop_reason": "pause_turn",
                "content": [{"type": "text", "text": "{}"}],
            },
            {},
            latency_ms=1,
        )
    assert raised.value.failure.details["reason"] == "structured_output_unexpected_stop"

    monkeypatch.setattr(
        "foundry_lite.infrastructure.adapters.anthropic_message_codec._MAX_REQUEST_BYTES",
        1,
    )
    with pytest.raises(AdapterError) as raised:
        build_anthropic_payload(replace(_request(), response_schema=None), None)
    assert raised.value.failure.details["reason"] == "request_too_large"
