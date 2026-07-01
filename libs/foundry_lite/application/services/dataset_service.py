"""Application service helpers for dataset service workflows."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.base import CoreService, build_service
from foundry_lite.application.services.dataset.ingest import DatasetIngestService
from foundry_lite.application.services.dataset.quality import DatasetQualityService
from foundry_lite.application.services.dataset.recovery import DatasetRecoveryService
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
    recovery: DatasetRecoveryService
    registry: DatasetRegistryService
    transaction: DatasetTransactionService
    version: DatasetVersionService

    @classmethod
    def create(cls, dependencies: CoreDependencies) -> DatasetServices:
        return cls(
            ingest=build_service(DatasetIngestService, dependencies),
            quality=build_service(DatasetQualityService, dependencies),
            recovery=build_service(DatasetRecoveryService, dependencies),
            registry=build_service(DatasetRegistryService, dependencies),
            transaction=build_service(DatasetTransactionService, dependencies),
            version=build_service(DatasetVersionService, dependencies),
        )

    def items(self) -> tuple[CoreService, ...]:
        return (self.ingest, self.quality, self.recovery, self.registry, self.transaction, self.version)
