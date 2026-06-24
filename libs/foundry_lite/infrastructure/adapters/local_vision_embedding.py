"""Local CLIP vision embedding adapter (Media/Content Plane L11).

The image/text engines are injectable — the defaults raise ``vision_model_unavailable``
because no model is bundled, so deterministic unit tests inject a pure-python fake and no
heavy ML dependency enters those tests. A real profile injects the fastembed CLIP pair
(``Qdrant/clip-ViT-B-32-vision`` + ``Qdrant/clip-ViT-B-32-text``) — a MATCHED contrastive pair
sharing ONE 512-dim space (ONNX, no torch), the open-source equivalent of Foundry's provided
SigLIP2 image-embedding model. ``is_available`` reflects whether real engines were injected.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from importlib import import_module
from typing import Any

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.embedding_model import EmbeddingModelError, EmbeddingVector

# fastembed CLIP image+text towers (matched pair, ONE 512-dim shared space, no torch). The
# version string is pinned into the index generation so a model upgrade re-projects rather than
# silently mixing vector spaces (the same fail-closed pinning the bge text generation uses).
_CLIP_IMAGE_MODEL_NAME = "Qdrant/clip-ViT-B-32-vision"
_CLIP_TEXT_MODEL_NAME = "Qdrant/clip-ViT-B-32-text"
CLIP_MODEL_VERSION = "clip-ViT-B-32"

VisionImageEngine = Callable[[Sequence[str]], Sequence[EmbeddingVector]]
VisionTextEngine = Callable[[Sequence[str]], Sequence[EmbeddingVector]]


def _default_vision_image_engine(image_paths: Sequence[str]) -> Sequence[EmbeddingVector]:
    # No CLIP model is bundled by default; a real profile injects one. Tests inject a fake.
    del image_paths
    raise EmbeddingModelError("vision_model_unavailable")


def _default_vision_text_engine(texts: Sequence[str]) -> Sequence[EmbeddingVector]:
    del texts
    raise EmbeddingModelError("vision_model_unavailable")


_clip_image_model: Any | None = None
_clip_text_model: Any | None = None


def _load_clip_image_model() -> Any:
    # Build the ONNX image tower lazily/once (module-level cache); fastembed downloads it from
    # HuggingFace on first use (cached in ~/.cache/huggingface), like bge/whisper.
    global _clip_image_model
    if _clip_image_model is None:
        fastembed_module = import_module("fastembed")
        _clip_image_model = fastembed_module.ImageEmbedding(_CLIP_IMAGE_MODEL_NAME)
    return _clip_image_model


def _load_clip_text_model() -> Any:
    global _clip_text_model
    if _clip_text_model is None:
        fastembed_module = import_module("fastembed")
        _clip_text_model = fastembed_module.TextEmbedding(_CLIP_TEXT_MODEL_NAME)
    return _clip_text_model


def _fastembed_clip_image_engine(image_paths: Sequence[str]) -> list[EmbeddingVector]:
    # Real engine (L11): one 512-dim image vector per frame path, in order, sharing the CLIP
    # space with the text tower so a text query can rank frames by visual content.
    model = _load_clip_image_model()
    return [tuple(vector.tolist()) for vector in model.embed(list(image_paths))]


def _fastembed_clip_text_engine(texts: Sequence[str]) -> list[EmbeddingVector]:
    model = _load_clip_text_model()
    return [tuple(vector.tolist()) for vector in model.embed(list(texts))]


class LocalVisionEmbeddingAdapter:
    """``VisionEmbeddingModelAdapter`` whose CLIP engines are injected (profile ``local-vision-embedding``)."""

    profile_name = "local-vision-embedding"

    def __init__(
        self,
        *,
        image_engine: VisionImageEngine | None = None,
        text_engine: VisionTextEngine | None = None,
        model_version: str = CLIP_MODEL_VERSION,
    ) -> None:
        self._image_engine = image_engine or _default_vision_image_engine
        self._text_engine = text_engine or _default_vision_text_engine
        self.is_available = image_engine is not None and text_engine is not None
        self.model_version = model_version

    def embed_images(self, image_paths: Sequence[str]) -> tuple[EmbeddingVector, ...]:
        return tuple(self._image_engine(image_paths))

    def embed_query(self, text: str) -> EmbeddingVector:
        return tuple(self._text_engine([text]))[0]

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "embed",
                    "unavailable",
                    True,
                    "No CLIP vision model is available; a real vision model profile must be configured.",
                ),
            ),
        )
