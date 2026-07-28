"""Media-plane dependency bundle and its narrowly owned port types."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.ports.content_index import ContentIndexAdapter
from foundry_lite.application.ports.external_media_reader import ExternalMediaReader
from foundry_lite.application.ports.media_access_cache_repository import MediaAccessCacheRepository
from foundry_lite.application.ports.media_derivative_repository import MediaDerivativeRepository
from foundry_lite.application.ports.media_preview_renderer import MediaPreviewRendererAdapter
from foundry_lite.application.ports.media_processor import MediaProcessorAdapter
from foundry_lite.application.ports.media_processor_registry import MediaProcessorRegistry
from foundry_lite.application.ports.media_reference_binding_repository import MediaReferenceBindingRepository
from foundry_lite.application.ports.media_repository import MediaRepository
from foundry_lite.application.ports.media_storage import MediaStorageAdapter


@dataclass(frozen=True)
class MediaDependencies:
    """Infrastructure ports owned by the Media and Content plane."""

    media_repository: MediaRepository
    media_derivative_repository: MediaDerivativeRepository
    media_reference_binding_repository: MediaReferenceBindingRepository
    media_access_cache_repository: MediaAccessCacheRepository
    media_storage: MediaStorageAdapter
    media_processor: MediaProcessorAdapter
    media_preview_renderer: MediaPreviewRendererAdapter
    external_media_reader: ExternalMediaReader
    content_index_adapter: ContentIndexAdapter
    media_processor_registry: MediaProcessorRegistry | None = None


__all__ = [
    "ContentIndexAdapter",
    "ExternalMediaReader",
    "MediaAccessCacheRepository",
    "MediaDependencies",
    "MediaDerivativeRepository",
    "MediaPreviewRendererAdapter",
    "MediaProcessorAdapter",
    "MediaProcessorRegistry",
    "MediaReferenceBindingRepository",
    "MediaRepository",
    "MediaStorageAdapter",
]
