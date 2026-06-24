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
from importlib import import_module
from typing import Any

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.embedding_model import (
    EmbeddingEngine,
    EmbeddingModelError,
    EmbeddingVector,
)

_DEFAULT_MODEL_VERSION = "local-v0"
# Real engine (L4): the fastembed BAAI/bge-small-en-v1.5 ONNX model (384-dim, no torch).
# The version string is pinned into the index generation so a model upgrade re-projects.
_FASTEMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"
FASTEMBED_MODEL_VERSION = "bge-small-en-v1.5"


def _default_embedding_engine(texts: Sequence[str]) -> Sequence[EmbeddingVector]:
    # No embedding model is bundled by default; a real profile injects one. The injectable
    # seam is what M8 proves; L4 wires `_fastembed_embedding_engine` into the composed runtime.
    del texts
    raise EmbeddingModelError("embedding_model_unavailable")


_fastembed_model: Any | None = None


def _load_fastembed_model() -> Any:
    # Build the ONNX model lazily/once (module-level cache) so repeated calls don't reload
    # the weights. fastembed downloads the model from HuggingFace on first use (cached in
    # ~/.cache/huggingface), like faster-whisper, and uses onnxruntime (no torch).
    global _fastembed_model
    if _fastembed_model is None:
        fastembed_module = import_module("fastembed")
        _fastembed_model = fastembed_module.TextEmbedding(_FASTEMBED_MODEL_NAME)
    return _fastembed_model


def _fastembed_embedding_engine(texts: Sequence[str]) -> list[EmbeddingVector]:
    # Real engine (L4): lazily import fastembed so the module has no import-time ML dependency.
    # Returns one 384-dim vector per text, in order, as an `EmbeddingVector` (tuple[float, ...]).
    model = _load_fastembed_model()
    return [tuple(vector.tolist()) for vector in model.embed(list(texts))]


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
