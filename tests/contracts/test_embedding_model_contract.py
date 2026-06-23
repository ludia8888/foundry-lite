"""Contract for ``EmbeddingModelAdapter`` (ADR-0001 invariant 5 / doc §10.2).

An embedding is a derived projection of content-unit text, pinned to a model version, and
produced in batch (N+1 prevention). We lock the Protocol shape: ``model_version`` /
``is_available`` attributes, batch ``embed`` returning one vector per input, and a typed
``failure_contract``.
"""

from __future__ import annotations

from collections.abc import Sequence

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.embedding_model import (
    EmbeddingModelAdapter,
    EmbeddingVector,
)


class _FakeEmbeddingModel:
    """In-memory implementation pinning the embed/contract shape."""

    model_version = "fake-v1"
    is_available = True

    def embed(self, texts: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        return tuple((float(len(text)),) for text in texts)

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile="fake-embedding", modes=())


def test_embedding_model_batch_shape() -> None:
    adapter: EmbeddingModelAdapter = _FakeEmbeddingModel()
    vectors = adapter.embed(["a", "bcd"])

    assert adapter.is_available is True
    assert adapter.model_version == "fake-v1"
    assert vectors == ((1.0,), (3.0,))
    assert adapter.failure_contract().adapter_profile == "fake-embedding"
