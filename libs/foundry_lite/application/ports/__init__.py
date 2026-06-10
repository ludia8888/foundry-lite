"""Application port contracts used by infrastructure adapters."""

from foundry_lite.application.ports.compute_adapter import ComputeAdapter
from foundry_lite.application.ports.dataset_repository import (
    DatasetAlreadyExistsError,
    DatasetRepository,
)
from foundry_lite.application.ports.dataset_storage import (
    DatasetStorageAdapter,
    StoredDatasetCommit,
)
from foundry_lite.application.ports.dataset_transaction_repository import (
    DatasetFileRecord,
    DatasetRunKind,
    DatasetTransactionRecord,
    DatasetTransactionRepository,
    DatasetVersionRecord,
)
from foundry_lite.application.ports.dataset_version_repository import DatasetVersionRepository
from foundry_lite.application.ports.materialization_repository import (
    MaterializationRecord,
    MaterializationRepository,
    MaterializationRunRecord,
)
from foundry_lite.application.ports.metadata_repository import MetadataRepository
from foundry_lite.application.ports.object_index_repository import (
    IndexRunRecord,
    ObjectConflictRecord,
    ObjectIndexRepository,
    ObjectLinkInsert,
    ObjectRecordInsert,
    ObjectRecordSourceUpdate,
)
from foundry_lite.application.ports.object_read_repository import ObjectReadRepository
from foundry_lite.application.ports.object_set_repository import (
    ObjectSetRecord,
    ObjectSetRepository,
)
from foundry_lite.application.ports.runtime_repository import (
    AuditEventRecord,
    LineageEdgeRecord,
    OutboxEventRecord,
    RuntimeLookupTable,
    RuntimeRepository,
    RuntimeRowsTable,
)
from foundry_lite.application.ports.transform_repository import (
    TransformRecord,
    TransformRepository,
    TransformRunRecord,
)

__all__ = [
    "AuditEventRecord",
    "ComputeAdapter",
    "DatasetAlreadyExistsError",
    "DatasetFileRecord",
    "DatasetRepository",
    "DatasetRunKind",
    "DatasetStorageAdapter",
    "DatasetTransactionRecord",
    "DatasetTransactionRepository",
    "DatasetVersionRepository",
    "DatasetVersionRecord",
    "IndexRunRecord",
    "LineageEdgeRecord",
    "MaterializationRecord",
    "MaterializationRepository",
    "MaterializationRunRecord",
    "MetadataRepository",
    "ObjectConflictRecord",
    "ObjectIndexRepository",
    "ObjectLinkInsert",
    "ObjectReadRepository",
    "ObjectRecordInsert",
    "ObjectRecordSourceUpdate",
    "ObjectSetRecord",
    "ObjectSetRepository",
    "OutboxEventRecord",
    "RuntimeLookupTable",
    "RuntimeRepository",
    "RuntimeRowsTable",
    "StoredDatasetCommit",
    "TransformRecord",
    "TransformRepository",
    "TransformRunRecord",
]
