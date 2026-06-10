from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.ingest import DatasetIngestService
from foundry_lite.application.services.dataset.quality import DatasetQualityService
from foundry_lite.application.services.dataset.registry import DatasetRegistryService
from foundry_lite.application.services.dataset.transactions import DatasetTransactionService
from foundry_lite.application.services.dataset.versions import DatasetVersionService


@dataclass(frozen=True)
class DatasetServices:
    """Dataset application service group.

    This groups dataset-specific constructor-injected services without
    reintroducing multiple inheritance.
    """

    ingest: DatasetIngestService
    quality: DatasetQualityService
    registry: DatasetRegistryService
    transaction: DatasetTransactionService
    version: DatasetVersionService

    @classmethod
    def create(cls, dependencies: CoreDependencies) -> DatasetServices:
        return cls(
            ingest=DatasetIngestService(dependencies),
            quality=DatasetQualityService(dependencies),
            registry=DatasetRegistryService(dependencies),
            transaction=DatasetTransactionService(dependencies),
            version=DatasetVersionService(dependencies),
        )

    def items(self) -> tuple[CoreService, ...]:
        return (self.ingest, self.quality, self.registry, self.transaction, self.version)
