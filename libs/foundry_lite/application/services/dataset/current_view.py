"""Resolve the logical file view produced by SNAPSHOT and APPEND transactions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, cast

from foundry_lite.application.ports import (
    DatasetManifest,
    DatasetStorageAdapter,
    DatasetTransactionRepository,
    DatasetVersionRow,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.services.dataset.storage_consistency import (
    committed_manifest,
    committed_version_file_paths,
    committed_version_preview_file_paths,
)
from foundry_lite.domain.errors import InvariantViolation


class DatasetVersionChainLookup(Protocol):
    def _version_by_id(self, conn: TransactionContext, version_id: str) -> DatasetVersionRow: ...


def current_view_versions(
    engine: TransactionManager,
    repository: DatasetTransactionRepository,
    version_service: DatasetVersionChainLookup,
    version: DatasetVersionRow,
) -> list[DatasetVersionRow]:
    """Return the current dataset view from the latest SNAPSHOT through APPENDs."""
    with engine.begin() as conn:
        return _current_view_versions(conn, repository, version_service, version)


def _current_view_versions(
    conn: TransactionContext,
    repository: DatasetTransactionRepository,
    version_service: DatasetVersionChainLookup,
    version: DatasetVersionRow,
) -> list[DatasetVersionRow]:
    chain: list[DatasetVersionRow] = []
    current = version
    seen: set[str] = set()
    while current["id"] not in seen:
        seen.add(current["id"])
        chain.append(current)
        transaction = repository.committed_transaction_by_version(
            transaction=conn,
            tenant_id=current["tenant_id"],
            committed_version_id=current["id"],
        )
        if transaction is None or transaction["tx_type"] != "APPEND" or transaction["base_version_id"] is None:
            break
        current = version_service._version_by_id(conn, transaction["base_version_id"])
        _ensure_same_dataset_view(version, current)
    else:
        raise InvariantViolation("dataset version ancestry contains a cycle", details={"version_id": version["id"]})
    chain.reverse()
    return chain


def _ensure_same_dataset_view(target: DatasetVersionRow, ancestor: DatasetVersionRow) -> None:
    identity = (target["tenant_id"], target["dataset_id"], target["branch"])
    ancestor_identity = (ancestor["tenant_id"], ancestor["dataset_id"], ancestor["branch"])
    if ancestor_identity != identity:
        raise InvariantViolation(
            "dataset APPEND ancestry crosses a dataset boundary",
            details={"version_id": target["id"], "ancestor_version_id": ancestor["id"]},
        )


def current_view_file_paths(
    storage: DatasetStorageAdapter,
    versions: Sequence[DatasetVersionRow],
    *,
    partition_filter: Mapping[str, object] | None = None,
    is_preview: bool = False,
) -> list[Path]:
    resolver = committed_version_preview_file_paths if is_preview else committed_version_file_paths
    return [path for version in versions for path in resolver(storage, version, partition_filter=partition_filter)]


def current_view_manifest(
    storage: DatasetStorageAdapter,
    versions: Sequence[DatasetVersionRow],
) -> DatasetManifest:
    latest = versions[-1]
    manifest = dict(committed_manifest(storage, latest["manifest_uri"]))
    manifest["files"] = [
        manifest_file
        for version in versions
        for manifest_file in committed_manifest(storage, version["manifest_uri"])["files"]
    ]
    return cast(DatasetManifest, manifest)


def current_view_version(versions: Sequence[DatasetVersionRow]) -> DatasetVersionRow:
    latest = versions[-1]
    return {
        **latest,
        "row_count": sum(version["row_count"] for version in versions),
        "byte_size": sum(version["byte_size"] for version in versions),
    }
