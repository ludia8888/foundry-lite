"""LocalEmbeddingAdapter (Media/Content Plane M8).

The engine is injectable: the default raises ``embedding_model_unavailable`` (no model is
bundled — live embeddings are deferred), and an injected deterministic fake covers the
batch-embed contract. ``is_available`` reflects whether a real engine was provided.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from foundry_lite.application.ports.embedding_model import EmbeddingModelError, EmbeddingVector
from foundry_lite.infrastructure.adapters.local_embedding import LocalEmbeddingAdapter


def _fake_engine(texts: Sequence[str]) -> list[EmbeddingVector]:
    return [(float(len(text)), 1.0) for text in texts]


def test_default_adapter_is_unavailable() -> None:
    adapter = LocalEmbeddingAdapter()
    assert adapter.is_available is False


def test_default_engine_raises_unavailable() -> None:
    adapter = LocalEmbeddingAdapter()
    with pytest.raises(EmbeddingModelError) as excinfo:
        adapter.embed(["anything"])
    assert excinfo.value.reason == "embedding_model_unavailable"


def test_injected_engine_embeds_in_batch_and_is_available() -> None:
    adapter = LocalEmbeddingAdapter(embedding_engine=_fake_engine, model_version="fake-v1")
    vectors = adapter.embed(["ab", "abcd"])

    assert adapter.is_available is True
    assert adapter.model_version == "fake-v1"
    assert vectors == ((2.0, 1.0), (4.0, 1.0))


def test_failure_contract_declares_unavailable_mode() -> None:
    contract = LocalEmbeddingAdapter().failure_contract()
    assert contract.adapter_profile == "local-embedding"
    assert {mode.kind for mode in contract.modes} == {"unavailable"}
