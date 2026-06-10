"""Application port contracts used by infrastructure adapters."""

from foundry_lite.application.ports.auth_provider import (
    AuthProvider,
    Credentials,
    Principal,
)
from foundry_lite.application.ports.compute_adapter import ComputeAdapter
from foundry_lite.application.ports.dataset_quality_repository import (
    DatasetCheckRecord,
    DatasetCheckResultRecord,
    DatasetQualityRepository,
    DatasetSchemaRecord,
)
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
    SyncRunRecord,
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
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.ports.transform_repository import (
    TransformRecord,
    TransformRepository,
    TransformRunRecord,
)

__all__ = [
    "AuditEventRecord",
    "AuthProvider",
    "ComputeAdapter",
    "Credentials",
    "DatasetAlreadyExistsError",
    "DatasetCheckRecord",
    "DatasetCheckResultRecord",
    "DatasetFileRecord",
    "DatasetQualityRepository",
    "DatasetRepository",
    "DatasetRunKind",
    "DatasetSchemaRecord",
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
    "Principal",
    "RuntimeLookupTable",
    "RuntimeRepository",
    "RuntimeRowsTable",
    "StoredDatasetCommit",
    "SyncRunRecord",
    "TransactionContext",
    "TransformRecord",
    "TransformRepository",
    "TransformRunRecord",
]
