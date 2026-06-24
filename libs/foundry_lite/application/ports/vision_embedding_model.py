"""Vision embedding model port (Media/Content Plane L11, doc §10.2 / ADR-0001 invariant 5).

CLIP-style contrastive models embed an IMAGE and a TEXT query into ONE shared vector space,
so a natural-language query ("a photo of a car") can rank a frame image by visual content —
zero-shot, open-vocabulary semantic search (Foundry's ``imageToEmbeddingsV1`` +
``nearestNeighbors`` pattern, palantir.com/docs/foundry/pb-functions-expression/
imageToEmbeddingsV1). This is a DIFFERENT model / DIFFERENT vector space from the lexical+dense
text embedding (bge): a visual query MUST be embedded with the vision model's TEXT tower, never
bge, and matched only inside the vision-model-pinned index generation. ``model_version`` is
pinned into that generation so the spaces never mix (fail-closed model pinning).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.embedding_model import EmbeddingVector


class VisionEmbeddingModelAdapter(Protocol):
    """Embeds frame images and text queries into ONE shared contrastive space, in batch."""

    model_version: str
    is_available: bool

    def embed_images(self, image_paths: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        """Return one image embedding per frame path, in order (N+1 prevention: batch)."""
        ...

    def embed_query(self, text: str) -> EmbeddingVector:
        """Return the text-tower embedding of a natural-language visual query."""
        ...

    def failure_contract(self) -> AdapterFailureContract:
        """Declare the typed failure modes this adapter promises to report."""
        ...
