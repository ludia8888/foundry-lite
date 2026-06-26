"""Governed language-model port (AIP-lite P0b — Model Gateway boundary).

All LLM access in app logic flows through the ``LanguageModelAdapter`` seam, never a provider
SDK. This mirrors Palantir's **provider-compatible proxy**: an app authenticates with a Foundry
token (never a provider API key) and the ``model`` field is a concrete Catalog RID, so no provider
string leaks into app logic (palantir.com/docs/foundry/aip/llm-provider-compatible-apis). Models
are referenced by a STABLE ALIAS that the gateway resolves to provider/model/revision — a faithful
EXTENSION of the documented SDK ``ModelInput(alias=..., model_version=...)`` indirection (omit
version → floating latest, supply → pinned revision; docs RECOMMEND pinning for production —
palantir.com/docs/foundry/integrate-models/transform-model-input). NOTE: Palantir has NO alias
indirection at the proxy layer (the proxy binds to a concrete RID); our alias→provider/model/
revision table extends ``ModelInput.alias`` to the gateway, it does not copy a proxy feature.

The real provider is DEFERRED (needs a Foundry token + network), exactly like other engines: the
default provider-compatible adapter raises ``language_model_unavailable``. Tests inject a
deterministic ``FakeLanguageModel``. The invocation contract (messages/params/tools → content +
token usage + finish_reason) mirrors the documented proxy contract.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract

ModelRole = Literal["system", "user", "assistant"]

_DEFAULT_MAX_OUTPUT_TOKENS = 1024
_DEFAULT_TIMEOUT_SECONDS = 60


class LanguageModelError(Exception):
    """Raised by a language-model engine when no model is bundled or a call is unsatisfiable."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ModelMessage:
    """One chat turn (documented proxy contract: role + content)."""

    role: ModelRole
    content: str


@dataclass(frozen=True)
class ModelToolCall:
    """A tool the model REQUESTED. Execution is server-side as the invoking user (P1 ToolBroker)."""

    tool_name: str
    arguments_json: str


@dataclass(frozen=True)
class ModelEvent:
    """One streamed increment of a completion (§8.1.2 ``stream``): a text delta + terminal reason."""

    delta_text: str
    finish_reason: str = ""


@dataclass(frozen=True)
class ModelRequest:
    """A governed invocation: messages + params + the egress facts the gateway gates on."""

    model_alias: str
    messages: tuple[ModelMessage, ...]
    tools: tuple[str, ...] = ()
    response_schema: str | None = None
    temperature: float = 0.0
    max_output_tokens: int = _DEFAULT_MAX_OUTPUT_TOKENS
    request_id: str = ""
    ai_run_id: str = ""
    # Egress governance inputs (our gateway gates these BEFORE calling the adapter): the data
    # classification leaving the boundary, and an optional region the call must be pinned to.
    data_classification: str = ""
    region_requirement: str | None = None
    timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class ModelResponse:
    """A governed response: provider/model/revision the gateway resolved + the proxy contract."""

    provider: str
    resolved_model_id: str
    resolved_model_revision: str
    content: str
    finish_reason: str
    input_tokens: int
    output_tokens: int
    normalized_tool_calls: tuple[ModelToolCall, ...] = ()
    provider_request_id: str = ""
    latency_ms: int = 0
    # Our invariant (NOT Palantir-documented): per-call hashes so a ledger can prove WHICH model
    # and WHICH prompt produced this response without persisting raw prompt content.
    model_hash: str = ""
    prompt_hash: str = ""


class LanguageModelAdapter(Protocol):
    """Single governed seam between app logic and a concrete provider-compatible proxy."""

    profile_name: str

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Return one governed completion for the request."""
        ...

    def stream(self, request: ModelRequest) -> Iterable[ModelEvent]:
        """Stream the same governed completion as ``complete`` as incremental ``ModelEvent``s."""
        ...

    def failure_contract(self) -> AdapterFailureContract:
        """Declare the typed failure modes this adapter promises to report."""
        ...
