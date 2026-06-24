from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.base import CoreService, build_service
from foundry_lite.application.services.media.access_patterns import MediaAccessPatternService
from foundry_lite.application.services.media.binding import MediaReferenceBindingService
from foundry_lite.application.services.media.catalog import MediaCatalogService
from foundry_lite.application.services.media.indexing import MediaIndexingService
from foundry_lite.application.services.media.processing import MediaProcessingService
from foundry_lite.application.services.media.references import MediaReferenceService
from foundry_lite.application.services.media.retention import MediaRetentionService
from foundry_lite.application.services.media.retrieval import DefaultContentRetrievalService
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.media.uploads import MediaUploadService
from foundry_lite.application.services.media.virtual_sets import VirtualMediaSetService
from foundry_lite.application.services.media.visual_search import MediaVisualSearchService


@dataclass(frozen=True)
class MediaServices:
    """Media/Content Plane application service group (parallel to ``DatasetServices``)."""

    catalog: MediaCatalogService
    transaction: MediaTransactionService
    upload: MediaUploadService
    reference: MediaReferenceService
    processing: MediaProcessingService
    indexing: MediaIndexingService
    retrieval: DefaultContentRetrievalService
    visual_search: MediaVisualSearchService
    binding: MediaReferenceBindingService
    access_pattern: MediaAccessPatternService
    virtual_sets: VirtualMediaSetService
    retention: MediaRetentionService

    @classmethod
    def create(cls, dependencies: CoreDependencies) -> MediaServices:
        return cls(
            catalog=build_service(MediaCatalogService, dependencies),
            transaction=build_service(MediaTransactionService, dependencies),
            upload=build_service(MediaUploadService, dependencies),
            reference=build_service(MediaReferenceService, dependencies),
            processing=build_service(MediaProcessingService, dependencies),
            indexing=build_service(MediaIndexingService, dependencies),
            retrieval=build_service(DefaultContentRetrievalService, dependencies),
            visual_search=build_service(MediaVisualSearchService, dependencies),
            binding=build_service(MediaReferenceBindingService, dependencies),
            access_pattern=build_service(MediaAccessPatternService, dependencies),
            virtual_sets=build_service(VirtualMediaSetService, dependencies),
            retention=build_service(MediaRetentionService, dependencies),
        )

    def items(self) -> tuple[CoreService, ...]:
        return (
            self.catalog,
            self.transaction,
            self.upload,
            self.reference,
            self.processing,
            self.indexing,
            self.retrieval,
            self.visual_search,
            self.binding,
            self.access_pattern,
            self.virtual_sets,
            self.retention,
        )
