"""Contract for ``VisionEmbeddingModelAdapter`` (Media/Content Plane L11).

A CLIP-style vision adapter embeds an IMAGE and a TEXT query into ONE shared contrastive space,
pinned to a model version (separate from the bge text space). We lock the Protocol shape:
``model_version`` / ``is_available`` attributes, batch ``embed_images`` returning one vector per
frame path, a single ``embed_query`` text-tower vector, and a typed ``failure_contract``.
"""

from __future__ import annotations

from collections.abc import Sequence

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.embedding_model import EmbeddingVector
from foundry_lite.application.ports.vision_embedding_model import VisionEmbeddingModelAdapter


class _FakeVisionModel:
    """In-memory implementation pinning the embed_images/embed_query/contract shape."""

    model_version = "fake-clip-v1"
    is_available = True

    def embed_images(self, image_paths: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        return tuple((float(len(path)), 0.0) for path in image_paths)

    def embed_query(self, text: str) -> EmbeddingVector:
        return (float(len(text)), 1.0)

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(adapter_profile="fake-vision-embedding", modes=())


def test_vision_embedding_model_shared_space_shape() -> None:
    adapter: VisionEmbeddingModelAdapter = _FakeVisionModel()
    image_vectors = adapter.embed_images(["a.png", "bcd.png"])
    query_vector = adapter.embed_query("a photo of a car")

    assert adapter.is_available is True
    assert adapter.model_version == "fake-clip-v1"
    assert image_vectors == ((5.0, 0.0), (7.0, 0.0))
    assert query_vector == (16.0, 1.0)
    assert adapter.failure_contract().adapter_profile == "fake-vision-embedding"
