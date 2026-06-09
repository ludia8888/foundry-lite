from __future__ import annotations

from foundry_lite.application.services.dataset.ingest import DatasetIngestMixin
from foundry_lite.application.services.dataset.quality import DatasetQualityMixin
from foundry_lite.application.services.dataset.registry import DatasetRegistryMixin
from foundry_lite.application.services.dataset.transactions import DatasetTransactionMixin
from foundry_lite.application.services.dataset.versions import DatasetVersionMixin


class DatasetServiceMixin(
    DatasetRegistryMixin,
    DatasetIngestMixin,
    DatasetTransactionMixin,
    DatasetQualityMixin,
    DatasetVersionMixin,
):
    pass
