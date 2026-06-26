"""Deterministic fake ``LanguageModelAdapter`` (AIP-lite P0b — tests + composition default).

A pure-python echo model: it concatenates the user turns and returns a canned completion with
deterministic token counts, so the gateway's resolution + egress + hashing logic can be proven
without any heavy ML dependency, Foundry token, or network. This is the SAFE composition default
(``is_available`` semantics live on the deferred provider adapter; the fake is always available).
"""

from __future__ import annotations

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.language_model import ModelEvent, ModelRequest, ModelResponse


class FakeLanguageModel:
    """Echoing ``LanguageModelAdapter`` (profile ``fake-language-model``)."""

    profile_name = "fake-language-model"

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
        return f"echo: {self._user_text(request)}".strip()

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
