"""Governed language-model port (AIP-lite Model Gateway boundary).

Application logic talks only to ``LanguageModelAdapter``. The gateway resolves a stable model
alias into an immutable provider/model/secret-reference route and attaches that route to the
request before infrastructure is allowed to perform egress. Direct provider credentials remain
behind ``SecretProvider`` and never enter Pipeline graph configuration, API responses, or traces.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.domain.context import RequestContext

ModelRole = Literal["system", "user", "assistant"]
ModelThinkingMode = Literal["provider_default", "disabled", "adaptive"]

_DEFAULT_MAX_OUTPUT_TOKENS = 1024
_DEFAULT_TIMEOUT_SECONDS = 60


def _empty_source_locator() -> Mapping[str, object]:
    return {}


def _empty_pricing_json() -> Mapping[str, object]:
    return {}


class LanguageModelError(Exception):
    """Raised by a language-model engine when no model is bundled or a call is unsatisfiable."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ModelMediaReference:
    """One immutable media coordinate attached to a multimodal user message."""

    media_item_version_id: str
    mime_type: str
    content_hash: str
    source_locator: Mapping[str, object] = field(default_factory=_empty_source_locator)


@dataclass(frozen=True)
class ModelMediaContent:
    """Verified bytes for one immutable media reference."""

    media_item_version_id: str
    mime_type: str
    content_hash: str
    byte_size: int
    content: bytes = field(repr=False)


class ModelMediaContentResolver(Protocol):
    """Resolve committed, tenant-scoped media bytes for provider input."""

    def read(
        self,
        *,
        tenant_id: str,
        reference: ModelMediaReference,
        expected_classification: str,
        allowed_classifications: tuple[str, ...] | None,
    ) -> ModelMediaContent:
        """Return bounded bytes after metadata, security, hash, and storage verification."""
        ...


@dataclass(frozen=True)
class ModelMessage:
    """One chat turn with optional governed media references."""

    role: ModelRole
    content: str
    media_references: tuple[ModelMediaReference, ...] = ()


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
class ModelInvocationRoute:
    """Gateway-resolved provider route; contains secret NAME only, never a credential value."""

    tenant_id: str
    provider_type: str
    provider_profile: str
    provider_model_id: str
    catalog_model_id: str
    model_revision: str
    secret_ref: str
    capabilities: Mapping[str, object]
    context_limit: int
    output_limit: int


@dataclass(frozen=True)
class ModelResolution:
    """Serving alias resolution facts available without provider execution."""

    provider: str
    model_id: str
    revision: str
    pricing_json: Mapping[str, object] = field(default_factory=_empty_pricing_json)


@dataclass(frozen=True)
class ModelRequest:
    """A governed invocation: messages + params + the egress facts the gateway gates on."""

    model_alias: str
    messages: tuple[ModelMessage, ...]
    expected_model_id: str | None = None
    expected_model_revision: str | None = None
    # The deployment environment the alias resolves in (§10.1 unique key is tenant+alias+environment);
    # defaults to the production environment so a caller never silently resolves a non-prod alias.
    environment: str = "prod"
    resolved_route: ModelInvocationRoute | None = None
    tools: tuple[str, ...] = ()
    response_schema: str | None = None
    temperature: float = 0.0
    max_output_tokens: int = _DEFAULT_MAX_OUTPUT_TOKENS
    thinking_mode: ModelThinkingMode = "provider_default"
    request_id: str = ""
    ai_run_id: str = ""
    request_hash: str = ""
    model_call_attempt: int = 1
    # Egress governance inputs (our gateway gates these BEFORE calling the adapter): the data
    # classification leaving the boundary, and an optional region the call must be pinned to.
    data_classification: str = ""
    # Server-derived caller clearance for media egress. An empty tuple is the
    # fail-closed default; None is reserved for server-authorized full clearance.
    media_allowed_classifications: tuple[str, ...] | None = ()
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
    supported_provider_profiles: tuple[str, ...]

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Return one governed completion for the request."""
        ...

    def stream(self, request: ModelRequest) -> Iterable[ModelEvent]:
        """Stream the same governed completion as ``complete`` as incremental ``ModelEvent``s."""
        ...

    def failure_contract(self) -> AdapterFailureContract:
        """Declare the typed failure modes this adapter promises to report."""
        ...


class GovernedSemanticModelPort(Protocol):
    """Cross-context boundary for model calls that already passed AIP governance."""

    def resolve_model(
        self,
        ctx: RequestContext,
        model_alias: str,
        environment: str = "prod",
    ) -> ModelResolution:
        """Resolve the exact serving model revision without provider execution."""
        ...

    def invoke(self, ctx: RequestContext, request: ModelRequest) -> ModelResponse:
        """Resolve, egress-check, invoke, and return pinned model evidence."""
        ...
