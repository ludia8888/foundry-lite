from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.base import CoreService, build_service
from foundry_lite.application.services.media.catalog import MediaCatalogService
from foundry_lite.application.services.media.references import MediaReferenceService
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.media.uploads import MediaUploadService


@dataclass(frozen=True)
class MediaServices:
    """Media/Content Plane application service group (parallel to ``DatasetServices``)."""

    catalog: MediaCatalogService
    transaction: MediaTransactionService
    upload: MediaUploadService
    reference: MediaReferenceService

    @classmethod
    def create(cls, dependencies: CoreDependencies) -> MediaServices:
        return cls(
            catalog=build_service(MediaCatalogService, dependencies),
            transaction=build_service(MediaTransactionService, dependencies),
            upload=build_service(MediaUploadService, dependencies),
            reference=build_service(MediaReferenceService, dependencies),
        )

    def items(self) -> tuple[CoreService, ...]:
        return (self.catalog, self.transaction, self.upload, self.reference)
