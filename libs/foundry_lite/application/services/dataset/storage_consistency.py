from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from foundry_lite.application.ports import (
    DatasetManifest,
    DatasetStorageAdapter,
    DatasetTransactionRow,
    DatasetVersionRow,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation


def cleanup_staging_transaction(
    storage: DatasetStorageAdapter,
    ctx: RequestContext,
    tx: DatasetTransactionRow | None,
    transaction_id: str,
) -> dict[str, object]:
    if tx is None or tx["tenant_id"] != ctx.tenant_id:
        return {"transaction_id": transaction_id, "removed": False, "reason": "transaction_not_found"}
    removed = storage.delete_staging_transaction(
        tenant_id=ctx.tenant_id,
        dataset_id=str(tx["dataset_id"]),
        transaction_id=transaction_id,
    )
    return {"transaction_id": transaction_id, "removed": removed}


def committed_version_file_path(
    storage: DatasetStorageAdapter,
    version: DatasetVersionRow,
) -> Path:
    return committed_version_file_paths(storage, version)[0]


def committed_version_file_paths(
    storage: DatasetStorageAdapter,
    version: DatasetVersionRow,
    *,
    partition_filter: Mapping[str, object] | None = None,
) -> list[Path]:
    try:
        return storage.data_file_paths(version["manifest_uri"], partition_filter=partition_filter)
    except FileNotFoundError as exc:
        raise version_storage_error(storage, version, "committed_version_storage_missing", str(exc)) from exc
    except (IndexError, KeyError, ValueError) as exc:
        raise version_storage_error(storage, version, "committed_version_storage_corrupt", str(exc)) from exc


def committed_version_preview_file_paths(
    storage: DatasetStorageAdapter,
    version: DatasetVersionRow,
    *,
    partition_filter: Mapping[str, object] | None = None,
) -> list[Path]:
    try:
        return storage.preview_file_paths(version["manifest_uri"], partition_filter=partition_filter)
    except FileNotFoundError as exc:
        raise version_storage_error(storage, version, "committed_version_storage_missing", str(exc)) from exc
    except (IndexError, KeyError, ValueError) as exc:
        raise version_storage_error(storage, version, "committed_version_storage_corrupt", str(exc)) from exc


def committed_manifest(storage: DatasetStorageAdapter, manifest_uri: str) -> DatasetManifest:
    try:
        return storage.load_manifest(manifest_uri)
    except FileNotFoundError as exc:
        raise manifest_storage_error(storage, manifest_uri, "committed_version_storage_missing", str(exc)) from exc
    except (IndexError, KeyError, ValueError) as exc:
        raise manifest_storage_error(storage, manifest_uri, "committed_version_storage_corrupt", str(exc)) from exc


def version_storage_error(
    storage: DatasetStorageAdapter,
    version: DatasetVersionRow,
    error_type: str,
    reason: str,
) -> InvariantViolation:
    details: dict[str, object] = {
        "error_type": error_type,
        "dataset_id": str(version["dataset_id"]),
        "version_id": str(version["id"]),
        "manifest_uri": str(version["manifest_uri"]),
        "storage_profile": storage.profile_name,
        "reason": reason,
    }
    return InvariantViolation("committed dataset version storage is inconsistent", details=details)


def manifest_storage_error(
    storage: DatasetStorageAdapter,
    manifest_uri: str,
    error_type: str,
    reason: str,
) -> InvariantViolation:
    details: dict[str, object] = {
        "error_type": error_type,
        "manifest_uri": manifest_uri,
        "storage_profile": storage.profile_name,
        "reason": reason,
    }
    return InvariantViolation("committed dataset manifest is inconsistent", details=details)
