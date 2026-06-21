from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from foundry_lite.application.ports import DatasetSchemaReference, DatasetVersionConflictError
from foundry_lite.application.primitives import StagedFileStats, _file_hash
from foundry_lite.application.services.dataset.schema_evolution import DatasetSchemaEvolutionResult
from foundry_lite.application.services.dataset.transactions import DatasetTransactionService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation, ValidationFailed
from foundry_lite.infrastructure.adapters import LocalDatasetStorageAdapter


class _ConflictTransactionRepository:
    def __init__(self, *, base_version_id: str | None = None) -> None:
        self.lock_calls: list[tuple[str, str]] = []
        self.abort_called = False
        self.insert_file_called = False
        self.commit_transaction_called = False
        self.base_version_id = base_version_id

    def transaction_by_id(self, *, transaction: object, transaction_id: str) -> dict[str, Any] | None:
        del transaction, transaction_id
        return {
            "id": "dstx_conflict",
            "tenant_id": "tenant-demo",
            "dataset_id": "ds_orders",
            "branch": "main",
            "tx_type": "SNAPSHOT",
            "status": "OPEN",
            "base_version_id": self.base_version_id,
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

    def abort_transaction(
        self,
        *,
        transaction: object,
        tenant_id: str,
        transaction_id: str,
        metadata: dict[str, object],
    ) -> None:
        del transaction, tenant_id, transaction_id, metadata
        self.abort_called = True

    def insert_version(self, *, transaction: object, record: object) -> None:
        del transaction, record
        raise DatasetVersionConflictError("duplicate version")

    def insert_file(self, **_kwargs: object) -> None:
        self.insert_file_called = True

    def commit_transaction(self, **_kwargs: object) -> bool:
        self.commit_transaction_called = True
        return True


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
            content_hash=_file_hash(parquet_path),
            schema_json={"fields": [{"name": "order_id", "type": "string"}]},
            schema_hash="schema-hash-demo",
        )

    def _run_dataset_checks(self, *_args: object, **_kwargs: object) -> list[dict[str, object]]:
        return []

    def _schema_compatibility_error(self, *_args: object, **_kwargs: object) -> None:
        return None

    def _schema_evolution_result(self, *_args: object, **_kwargs: object) -> DatasetSchemaEvolutionResult:
        return DatasetSchemaEvolutionResult(failure=None, metadata=None)

    def _ensure_schema(self, *_args: object, **_kwargs: object) -> int:
        return 1

    def _ensure_schema_reference(self, *_args: object, **_kwargs: object) -> DatasetSchemaReference:
        return DatasetSchemaReference(schema_id="schema_v1", version=1)


class _SchemaRaceQuality(_Quality):
    def __init__(self, staged_path: Path, repository: _ConflictTransactionRepository) -> None:
        super().__init__(staged_path)
        self.repository = repository
        self.saw_lock_before_compatibility_check = False

    def _schema_evolution_result(self, *_args: object, **_kwargs: object) -> DatasetSchemaEvolutionResult:
        self.saw_lock_before_compatibility_check = bool(self.repository.lock_calls)
        return DatasetSchemaEvolutionResult(
            failure={"check": "schema_compatibility", "status": "failed", "reason": "latest_schema_changed"},
            metadata=None,
        )


class _VersionLookup:
    def __init__(self, *, latest_version_id: str | None = None) -> None:
        self.latest_version_id = latest_version_id

    def _next_dataset_version_number(self, _conn: object, dataset_id: str) -> int:
        assert dataset_id == "ds_orders"
        return 7

    def _latest_version_by_dataset_id(
        self, _conn: object, dataset_id: str, *, allow_missing: bool = False
    ) -> dict[str, Any] | None:
        del allow_missing
        assert dataset_id == "ds_orders"
        if self.latest_version_id is None:
            return None
        return {"id": self.latest_version_id}


class _Runtime:
    def _outbox(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("metadata conflict must not emit commit outbox")

    def _audit(self, *_args: object, **_kwargs: object) -> None:
        if _kwargs.get("action") == "abort":
            return
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


def test_dataset_finalize_rejects_stale_snapshot_base_before_commit(tmp_path: Path) -> None:
    staged = tmp_path / "staged.parquet"
    staged.write_bytes(b"fake parquet bytes")
    repository = _ConflictTransactionRepository(base_version_id="dsv_old")
    service = _service(tmp_path, staged, repository, latest_version_id="dsv_current")

    with pytest.raises(ConflictDetected, match="base version is stale") as exc_info:
        _finalize(service, staged)

    assert repository.lock_calls == [("tenant-demo", "ds_orders")]
    assert repository.insert_file_called is False
    assert repository.commit_transaction_called is False
    assert list((tmp_path / "object-storage").glob("**/version=*")) == []
    assert exc_info.value.details == {
        "transaction_id": "dstx_conflict",
        "dataset_id": "ds_orders",
        "expected_base_version_id": "dsv_old",
        "current_base_version_id": "dsv_current",
    }


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


def test_schema_compatibility_revalidates_if_latest_schema_changes(tmp_path: Path) -> None:
    staged = tmp_path / "staged.parquet"
    staged.write_bytes(b"fake parquet bytes")
    repository = _ConflictTransactionRepository()
    quality = _SchemaRaceQuality(staged, repository)
    service = _service(tmp_path, staged, repository, quality=quality)

    with pytest.raises(ValidationFailed, match="dataset checks failed") as exc_info:
        _finalize(service, staged)

    failures = exc_info.value.details["failures"]
    assert repository.lock_calls == [("tenant-demo", "ds_orders")]
    assert quality.saw_lock_before_compatibility_check is True
    assert repository.abort_called is True
    assert repository.insert_file_called is False
    assert repository.commit_transaction_called is False
    assert failures == [
        {
            "check": "schema_compatibility",
            "status": "failed",
            "reason": "latest_schema_changed",
            "contract_status": "BLOCK_COMMIT",
        }
    ]


def _service(
    tmp_path: Path,
    staged: Path,
    repository: _ConflictTransactionRepository,
    quality: _Quality | None = None,
    latest_version_id: str | None = None,
) -> DatasetTransactionService:
    storage = LocalDatasetStorageAdapter(tmp_path / "object-storage")
    service = DatasetTransactionService(
        engine=object(),
        dataset_storage=storage,
        dataset_transaction_repository=repository,
    )
    service.bind_collaborators(
        {
            "dataset_quality_service": quality or _Quality(staged),
            "dataset_version_service": _VersionLookup(latest_version_id=latest_version_id),
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
