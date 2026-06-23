"""Embedding model port (Media/Content Plane M8, doc §10.2 / ADR-0001 invariant 5).

An embedding is a derived/projected vector off an immutable content unit's text — never a
separate source truth. The adapter is the seam between the projection pipeline and a concrete
embedding model; the model version it reports is pinned into the index generation so a model
upgrade re-projects rather than silently mixing vector spaces. Embedding happens in batch
(N+1 prevention): one call embeds many content-unit texts.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract

EmbeddingVector = tuple[float, ...]
EmbeddingEngine = Callable[[Sequence[str]], Sequence[EmbeddingVector]]


class EmbeddingModelError(Exception):
    """Raised by an embedding engine when no model is bundled or the input is unembeddable."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class EmbeddingModelAdapter(Protocol):
    """Projects content-unit text into pinned-model vectors, in batch."""

    model_version: str
    is_available: bool

    def embed(self, texts: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        """Return one vector per input text, in order."""
        ...

    def failure_contract(self) -> AdapterFailureContract:
        """Declare the typed failure modes this adapter promises to report."""
        ...
