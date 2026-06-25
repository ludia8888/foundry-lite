"""Context provider port (AIP-lite P0d — retrieval context boundary, §8.5/§8.6).

The context provider is the boundary that hands already-authorized, versioned
retrieval results to the AIP context compiler. It does not build prompts and it
does not call an LLM. Each item carries the opaque ``context_id`` shown to the
model plus source/version/hash metadata used later for citations and
fail-closed validation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

from foundry_lite.domain.context import RequestContext

RetrievedContextKind = Literal["object", "document", "function"]


class ContextCompilationError(Exception):
    """Raised when context cannot be safely compiled for a model call."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


class ContextRetrievalError(Exception):
    """Raised when retrieval cannot safely produce authorized context."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def _empty_state_json() -> dict[str, object]:
    return {}


@dataclass(frozen=True)
class ContextRetrievalRequest:
    """Inputs used to retrieve context for one user turn."""

    tenant_id: str
    actor_user_id: str
    query: str
    agent_version_id: str
    ontology_version_id: str
    max_context_items: int
    max_context_tokens: int
    security_partition: str
    state_json: Mapping[str, object] = field(default_factory=_empty_state_json)


@dataclass(frozen=True)
class RetrievedContextItem:
    """One authorized context item, matching canonical §8.5 fields."""

    context_id: str
    kind: RetrievedContextKind
    text: str
    source_ref: str
    source_version: str
    content_hash: str
    relevance_score: float
    retrieval_method: str
    security_partition: str
    token_estimate: int


class ContextProvider(Protocol):
    """Retrieve authorized, versioned context items for the compiler."""

    def retrieve_context(
        self, *, ctx: RequestContext, request: ContextRetrievalRequest
    ) -> tuple[RetrievedContextItem, ...]:
        """Return context items already filtered for the actor and tenant."""
        ...
