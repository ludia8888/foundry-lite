"""Deterministic fake ``LanguageModelAdapter`` (AIP-lite P0b — tests + composition default).

A pure-python echo model: it concatenates the user turns and returns a canned completion with
deterministic token counts, so the gateway's resolution + egress + hashing logic can be proven
without any heavy ML dependency, Foundry token, or network. This is the SAFE composition default
(``is_available`` semantics live on the deferred provider adapter; the fake is always available).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.language_model import ModelEvent, ModelRequest, ModelResponse


class FakeLanguageModel:
    """Echoing ``LanguageModelAdapter`` (profile ``fake-language-model``)."""

    profile_name = "fake-language-model"
    supported_provider_profiles: tuple[str, ...] = ("fake-language-model", "local-fake")

    def complete(self, request: ModelRequest) -> ModelResponse:
        content = self._echo(request)
        return ModelResponse(
            provider="fake",
            resolved_model_id="",
            resolved_model_revision="",
            content=content,
            finish_reason="stop",
            input_tokens=_token_count(self._user_text(request)),
            output_tokens=_token_count(content),
            normalized_tool_calls=(),
            provider_request_id="fake-request",
            latency_ms=0,
        )

    def stream(self, request: ModelRequest) -> list[ModelEvent]:
        # Deterministic stream: one event carrying the full completion + the terminal finish_reason,
        # so the streamed content equals ``complete``'s content for the same request.
        return [ModelEvent(delta_text=self._echo(request), finish_reason="stop")]

    def _user_text(self, request: ModelRequest) -> str:
        return " ".join(message.content for message in request.messages if message.role == "user")

    def _echo(self, request: ModelRequest) -> str:
        answer = f"echo: {self._user_text(request)}".strip()
        context_id = _first_citation_context_id(request) if _schema_requests_citations(request.response_schema) else ""
        if not context_id:
            return answer
        return json.dumps(
            {
                "answer": answer,
                "citations": [
                    {
                        "contextId": context_id,
                        "claimSpan": {"start": 0, "end": len(answer)},
                        "citationOrder": 1,
                    }
                ],
            },
            sort_keys=True,
        )

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "complete",
                    "validation",
                    False,
                    "The fake language model received a malformed request.",
                ),
            ),
        )


def _token_count(text: str) -> int:
    return len(text.split())


def _schema_requests_citations(response_schema: str | None) -> bool:
    if not response_schema:
        return False
    try:
        payload = json.loads(response_schema)
    except json.JSONDecodeError:
        return False
    return _contains_key(payload, "citations")


def _first_citation_context_id(request: ModelRequest) -> str:
    citations = _citation_rows(request)
    if not citations:
        return ""
    first = citations[0]
    context_id = first.get("context_id") if isinstance(first, Mapping) else None
    return context_id if isinstance(context_id, str) else ""


def _citation_rows(request: ModelRequest) -> Sequence[object]:
    payload = _citation_mapping_payload(request)
    citations = payload.get("citations") if payload is not None else None
    if not isinstance(citations, Sequence) or isinstance(citations, str | bytes):
        return ()
    return citations


def _citation_mapping_payload(request: ModelRequest) -> Mapping[str, object] | None:
    block = _citation_mapping_block(_system_content(request))
    if block == "":
        return None
    try:
        payload = json.loads(block)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, Mapping) else None


def _system_content(request: ModelRequest) -> str:
    return next((message.content for message in request.messages if message.role == "system"), "")


def _citation_mapping_block(system: str) -> str:
    marker = "## citation_mapping\n"
    start = system.find(marker)
    if start == -1:
        return ""
    start += len(marker)
    end = system.find("\n\n## ", start)
    return system[start:] if end == -1 else system[start:end]


def _contains_key(value: object, key: str) -> bool:
    if isinstance(value, Mapping):
        return key in value or any(_contains_key(child, key) for child in value.values())
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return any(_contains_key(child, key) for child in value)
    return False
