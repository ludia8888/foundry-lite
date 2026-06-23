from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.base import CoreService, build_service
from foundry_lite.application.services.media.binding import MediaReferenceBindingService
from foundry_lite.application.services.media.catalog import MediaCatalogService
from foundry_lite.application.services.media.indexing import MediaIndexingService
from foundry_lite.application.services.media.processing import MediaProcessingService
from foundry_lite.application.services.media.references import MediaReferenceService
from foundry_lite.application.services.media.retrieval import DefaultContentRetrievalService
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.media.uploads import MediaUploadService


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
    binding: MediaReferenceBindingService

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
            binding=build_service(MediaReferenceBindingService, dependencies),
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
            self.binding,
        )
