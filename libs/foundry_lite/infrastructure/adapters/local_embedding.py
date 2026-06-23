"""Local embedding model adapter (Media/Content Plane M8).

The embedding engine is injectable — the default raises ``embedding_model_unavailable``
because no embedding model is bundled (a real profile injects sentence-transformers /
a managed provider; live embeddings are deferred like live-OCR / live-ES). Tests inject a
deterministic pure-python fake, so no heavy ML dependency enters CI and there is no
CI-unreachable glue. ``is_available`` reflects whether a real engine was injected; when it
is False the indexing/retrieval services stay lexical-only.
"""

from __future__ import annotations

from collections.abc import Sequence

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.embedding_model import (
    EmbeddingEngine,
    EmbeddingModelError,
    EmbeddingVector,
)

_DEFAULT_MODEL_VERSION = "local-v0"


def _default_embedding_engine(texts: Sequence[str]) -> Sequence[EmbeddingVector]:
    # No embedding model is bundled; a real profile injects one (live embeddings deferred,
    # mirroring the live-OCR / live-ES deferral). The injectable seam is what M8 proves.
    del texts
    raise EmbeddingModelError("embedding_model_unavailable")


class LocalEmbeddingAdapter:
    """``EmbeddingModelAdapter`` whose engine is injected (profile ``local-embedding``)."""

    profile_name = "local-embedding"

    def __init__(
        self, *, embedding_engine: EmbeddingEngine | None = None, model_version: str = _DEFAULT_MODEL_VERSION
    ) -> None:
        self._embedding_engine = embedding_engine or _default_embedding_engine
        self.is_available = embedding_engine is not None
        self.model_version = model_version

    def embed(self, texts: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        return tuple(self._embedding_engine(texts))

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "embed",
                    "unavailable",
                    True,
                    "No embedding model is available; a real model profile must be configured.",
                ),
            ),
        )
