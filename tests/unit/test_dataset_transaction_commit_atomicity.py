from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from foundry_lite.application.ports import DatasetVersionConflictError
from foundry_lite.application.primitives import StagedFileStats
from foundry_lite.application.services.dataset.transactions import DatasetTransactionService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation
from foundry_lite.infrastructure.adapters import LocalDatasetStorageAdapter


class _ConflictTransactionRepository:
    def __init__(self) -> None:
        self.lock_calls: list[tuple[str, str]] = []
        self.insert_file_called = False
        self.commit_transaction_called = False

    def transaction_by_id(self, *, transaction: object, transaction_id: str) -> dict[str, Any] | None:
        del transaction, transaction_id
        return {
            "id": "dstx_conflict",
            "tenant_id": "tenant-demo",
            "dataset_id": "ds_orders",
            "branch": "main",
            "tx_type": "SNAPSHOT",
            "status": "OPEN",
            "base_version_id": None,
            "committed_version_id": None,
            "schema_version": None,
            "created_by": "user-demo",
            "created_at": "2026-06-13T00:00:00Z",
            "committed_at": None,
            "metadata": {},
        }

    def lock_dataset_for_version_allocation(self, *, transaction: object, tenant_id: str, dataset_id: str) -> None:
        del transaction
        self.lock_calls.append((tenant_id, dataset_id))

    def insert_version(self, *, transaction: object, record: object) -> None:
        del transaction, record
        raise DatasetVersionConflictError("duplicate version")

    def insert_file(self, **_kwargs: object) -> None:
        self.insert_file_called = True

    def commit_transaction(self, **_kwargs: object) -> None:
        self.commit_transaction_called = True


class _FileInsertFailureRepository(_ConflictTransactionRepository):
    def insert_version(self, *, transaction: object, record: object) -> None:
        del transaction, record

    def insert_file(self, **_kwargs: object) -> None:
        self.insert_file_called = True
        raise RuntimeError("dataset file insert exploded")


class _Quality:
    def __init__(self, staged_path: Path) -> None:
        self.staged_path = staged_path

    def _inspect_parquet(self, parquet_path: Path, primary_key: list[str]) -> StagedFileStats:
        assert parquet_path == self.staged_path
        assert primary_key == ["order_id"]
        return StagedFileStats(
            parquet_path=parquet_path,
            row_count=1,
            byte_size=parquet_path.stat().st_size,
            content_hash="content-hash-demo",
            schema_json={"fields": [{"name": "order_id", "type": "string"}]},
            schema_hash="schema-hash-demo",
        )

    def _run_dataset_checks(self, *_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return []

    def _schema_compatibility_error(self, *_args: object, **_kwargs: object) -> None:
        return None

    def _ensure_schema(self, *_args: object, **_kwargs: object) -> int:
        return 1


class _VersionLookup:
    def _next_dataset_version_number(self, _conn: object, dataset_id: str) -> int:
        assert dataset_id == "ds_orders"
        return 7


class _Runtime:
    def _outbox(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("metadata conflict must not emit commit outbox")

    def _audit(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("metadata conflict must not emit commit audit")


def test_dataset_finalize_cleans_orphan_artifacts_after_version_conflict(tmp_path: Path) -> None:
    staged = tmp_path / "staged.parquet"
    staged.write_bytes(b"fake parquet bytes")
    repository = _ConflictTransactionRepository()
    service = _service(tmp_path, staged, repository)

    with pytest.raises(ConflictDetected) as exc_info:
        _finalize(service, staged)

    assert repository.lock_calls == [("tenant-demo", "ds_orders")]
    assert repository.insert_file_called is False
    assert repository.commit_transaction_called is False
    assert list((tmp_path / "object-storage").glob("**/version=*")) == []
    assert exc_info.value.details["version_number"] == 7
    assert exc_info.value.details["orphan_cleanup"]["removed"] is True


def test_dataset_finalize_cleans_orphan_artifacts_after_file_persistence_failure(tmp_path: Path) -> None:
    staged = tmp_path / "staged.parquet"
    staged.write_bytes(b"fake parquet bytes")
    repository = _FileInsertFailureRepository()
    service = _service(tmp_path, staged, repository)

    with pytest.raises(InvariantViolation) as exc_info:
        _finalize(service, staged)

    assert repository.lock_calls == [("tenant-demo", "ds_orders")]
    assert repository.insert_file_called is True
    assert repository.commit_transaction_called is False
    assert list((tmp_path / "object-storage").glob("**/version=*")) == []
    assert exc_info.value.details["orphan_cleanup"]["removed"] is True


def _service(
    tmp_path: Path,
    staged: Path,
    repository: _ConflictTransactionRepository,
) -> DatasetTransactionService:
    storage = LocalDatasetStorageAdapter(tmp_path / "object-storage")
    service = DatasetTransactionService(
        engine=object(),
        dataset_storage=storage,
        dataset_transaction_repository=repository,
    )
    service.bind_collaborators(
        {
            "dataset_quality_service": _Quality(staged),
            "dataset_version_service": _VersionLookup(),
            "runtime_service": _Runtime(),
        }
    )
    return service


def _finalize(service: DatasetTransactionService, staged: Path) -> None:
    service._finalize_open_transaction(
        object(),
        RequestContext(roles=("admin",)),
        dataset=_dataset_row(),
        transaction_id="dstx_conflict",
        staged_parquet=staged,
        run_id="sync_conflict",
        audit_action="dataset.upload_csv",
        outbox_event_type="dataset.version.committed",
    )


def _dataset_row() -> dict[str, Any]:
    return {
        "id": "ds_orders",
        "tenant_id": "tenant-demo",
        "namespace": "raw",
        "name": "orders",
        "description": None,
        "storage_kind": "local",
        "storage_uri": None,
        "owner_team": "data",
        "classification": "internal",
        "status": "active",
        "primary_key": ["order_id"],
        "created_at": "2026-06-13T00:00:00Z",
        "updated_at": "2026-06-13T00:00:00Z",
    }
