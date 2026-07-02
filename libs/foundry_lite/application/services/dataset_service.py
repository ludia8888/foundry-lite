"""Application service helpers for dataset service workflows."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.base import CoreService, build_service
from foundry_lite.application.services.dataset.ingest import DatasetIngestService
from foundry_lite.application.services.dataset.quality import DatasetQualityRuntimeService
from foundry_lite.application.services.dataset.quality_api import DatasetQualityApiService
from foundry_lite.application.services.dataset.quality_contracts import DatasetQualityContractService
from foundry_lite.application.services.dataset.quality_service import DatasetQualityService
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
    quality_api: DatasetQualityApiService
    quality_contract: DatasetQualityContractService
    quality_runtime: DatasetQualityRuntimeService
    recovery: DatasetRecoveryService
    registry: DatasetRegistryService
    transaction: DatasetTransactionService
    version: DatasetVersionService

    @classmethod
    def create(cls, dependencies: CoreDependencies) -> DatasetServices:
        quality_api = build_service(DatasetQualityApiService, dependencies)
        quality_contract = build_service(DatasetQualityContractService, dependencies)
        quality_runtime = build_service(DatasetQualityRuntimeService, dependencies)
        return cls(
            ingest=build_service(DatasetIngestService, dependencies),
            quality=DatasetQualityService(
                dataset_quality_api_service=quality_api,
                dataset_quality_runtime_service=quality_runtime,
            ),
            quality_api=quality_api,
            quality_contract=quality_contract,
            quality_runtime=quality_runtime,
            recovery=build_service(DatasetRecoveryService, dependencies),
            registry=build_service(DatasetRegistryService, dependencies),
            transaction=build_service(DatasetTransactionService, dependencies),
            version=build_service(DatasetVersionService, dependencies),
        )

    def items(self) -> tuple[CoreService, ...]:
        return (
            self.ingest,
            self.quality_api,
            self.quality_contract,
            self.quality_runtime,
            self.recovery,
            self.registry,
            self.transaction,
            self.version,
        )
