"""Tenant-scoped Dataset lookups used by Pipeline artifact evidence."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import DatasetRepository, DatasetVersionRepository, TransactionContext
from foundry_lite.domain.errors import NotFound


def required_dataset_version(
    repository: DatasetVersionRepository,
    transaction: TransactionContext,
    tenant_id: str,
    version_id: str,
) -> Mapping[str, object]:
    row = repository.version_by_id(transaction=transaction, version_id=version_id)
    if row is None or row["tenant_id"] != tenant_id:
        raise NotFound("pipeline Dataset version not found", details={"version_id": version_id})
    return row


def required_dataset(
    repository: DatasetRepository,
    transaction: TransactionContext,
    tenant_id: str,
    dataset_id: str,
) -> Mapping[str, object]:
    row = repository.dataset_by_id(
        transaction=transaction,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
    )
    if row is None:
        raise NotFound("pipeline Dataset not found", details={"dataset_id": dataset_id})
    return row


def required_dataset_for_version(
    dataset_repository: DatasetRepository,
    version_repository: DatasetVersionRepository,
    transaction: TransactionContext,
    tenant_id: str,
    version_id: str,
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    version = required_dataset_version(version_repository, transaction, tenant_id, version_id)
    dataset = required_dataset(
        dataset_repository,
        transaction,
        tenant_id,
        str(version["dataset_id"]),
    )
    return version, dataset
