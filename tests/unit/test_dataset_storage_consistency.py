from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports import DatasetFileRecord
from foundry_lite.application.ports.transaction_context import StatusTransition
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import InvariantViolation, ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite.infrastructure.repositories import SqlAlchemyDatasetTransactionRepository
from sqlalchemy import func, select


class _FailingFileInsertRepository:
    def __init__(self, wrapped: SqlAlchemyDatasetTransactionRepository) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    def insert_file(self, *, transaction: object, record: DatasetFileRecord) -> None:
        del transaction, record
        raise RuntimeError("dataset file insert exploded after storage promotion")


class _FailingSyncRunCommitRepository:
    def __init__(self, wrapped: SqlAlchemyDatasetTransactionRepository) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    def update_sync_run_terminal(
        self,
        *,
        transaction: object,
        tenant_id: str,
        sync_run_id: str,
        transition: StatusTransition,
        committed_version_id: str | None,
        completed_at: str,
    ) -> bool:
        if transition.to_status == "COMMITTED":
            raise RuntimeError("sync run terminal update exploded after storage promotion")
        return self._wrapped.update_sync_run_terminal(
            transaction=transaction,
            tenant_id=tenant_id,
            sync_run_id=sync_run_id,
            transition=transition,
            committed_version_id=committed_version_id,
            completed_at=completed_at,
        )


def test_dataset_commit_storage_success_db_failure_creates_orphan_cleanup_evidence(tmp_path: Path) -> None:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "split-brain-runtime")
    failing = _FailingFileInsertRepository(dependencies.dataset_transaction_repository)
    foundry = FoundryLite(dependencies=replace(dependencies, dataset_transaction_repository=failing))
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])

    with pytest.raises(InvariantViolation) as exc_info:
        foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)

    cleanup = exc_info.value.details["orphan_cleanup"]
    assert cleanup["removed"] is True
    assert not list(dependencies.storage_root.glob("**/version=*"))
    failed_run = next(run for run in foundry.operations.list_runs(ctx=ctx)["syncRuns"] if run["status"] == "FAILED")
    error = failed_run["error"]
    assert error["type"] == "INVARIANT_VIOLATION"
    assert error["details"]["orphan_cleanup"]["manifest_uri"] == cleanup["manifest_uri"]


def test_sync_run_commit_failure_removes_promoted_storage_artifacts(tmp_path: Path) -> None:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "terminal-update-failure")
    failing = _FailingSyncRunCommitRepository(dependencies.dataset_transaction_repository)
    foundry = FoundryLite(dependencies=replace(dependencies, dataset_transaction_repository=failing))
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])

    with pytest.raises(InvariantViolation) as exc_info:
        foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)

    with dependencies.engine.begin() as conn:
        version_count = conn.execute(select(func.count()).select_from(db.dataset_versions)).scalar_one()
        file_count = conn.execute(select(func.count()).select_from(db.dataset_files)).scalar_one()
        transaction = conn.execute(select(db.dataset_transactions)).mappings().one()
        sync_run = conn.execute(select(db.sync_runs)).mappings().one()

    assert exc_info.value.details["orphan_cleanup"]["removed"] is True
    assert version_count == 0
    assert file_count == 0
    assert transaction["status"] == "ABORTED"
    assert sync_run["status"] == "FAILED"
    assert not list(dependencies.storage_root.glob("**/version=*"))


def test_blocked_quality_check_results_survive_upload_rollback(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "quality-evidence"))
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])

    with pytest.raises(ValidationFailed, match="dataset checks failed"):
        foundry.datasets.upload_csv("raw.orders", _duplicate_csv_file(tmp_path), ctx=ctx)

    with foundry.engine.begin() as conn:
        check_rows = conn.execute(select(db.dataset_check_results)).mappings().all()
        transaction = conn.execute(select(db.dataset_transactions)).mappings().one()
        sync_run = conn.execute(select(db.sync_runs)).mappings().one()

    blocking_checks = [row for row in check_rows if row["status"] == "BLOCK_COMMIT"]
    assert len(check_rows) >= 1
    assert len(blocking_checks) == 1
    assert blocking_checks[0]["details"]["check"] == "unique"
    assert transaction["status"] == "ABORTED"
    assert transaction["metadata"]["validationFailures"][0]["check"] == "unique"
    assert sync_run["status"] == "FAILED"
    assert sync_run["error"]["trace"]["adapter"] == "dataset_transaction.finalize"


def test_dataset_commit_db_success_manifest_missing_marks_storage_corruption(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "missing-manifest"))
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])
    committed = foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)
    Path(committed.manifest_uri).unlink()

    with pytest.raises(InvariantViolation) as exc_info:
        foundry.datasets.inspect("raw.orders", ctx=ctx, version=committed.version_id)

    details = exc_info.value.details
    assert details["error_type"] == "committed_version_storage_missing"
    assert details["dataset_ref"] == "raw.orders"
    assert details["version_id"] == committed.version_id
    assert details["manifest_uri"] == committed.manifest_uri


def test_dataset_preview_data_file_missing_marks_storage_corruption(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "missing-data-file"))
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])
    committed = foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)
    manifest = foundry.datasets.inspect("raw.orders", ctx=ctx, version=committed.version_id)["manifest"]
    Path(manifest["files"][0]["uri"]).unlink()

    with pytest.raises(InvariantViolation) as exc_info:
        foundry.datasets.preview("raw.orders", ctx=ctx, version=committed.version_id)

    details = exc_info.value.details
    assert details["error_type"] == "committed_version_storage_missing"
    assert details["dataset_id"]
    assert details["version_id"] == committed.version_id
    assert details["manifest_uri"] == committed.manifest_uri


def test_abort_cleanup_never_deletes_committed_manifest(tmp_path: Path) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "abort-cleanup"))
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])
    committed = foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)
    manifest_before = foundry.datasets.inspect("raw.orders", ctx=ctx, version=committed.version_id)["manifest"]

    with pytest.raises(ValidationFailed, match="dataset checks failed"):
        foundry.datasets.upload_csv("raw.orders", _duplicate_csv_file(tmp_path), ctx=ctx)

    manifest_after = foundry.datasets.inspect("raw.orders", ctx=ctx, version=committed.version_id)["manifest"]
    preview = foundry.datasets.preview("raw.orders", ctx=ctx, version=committed.version_id)
    assert Path(committed.manifest_uri).exists()
    assert manifest_after["files"] == manifest_before["files"]
    assert [(row["id"], row["amount"]) for row in preview] == [("O-1", 100)]


def test_partition_pruning_reads_only_required_manifest_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foundry = FoundryLite(dependencies=create_local_core_dependencies(storage_root=tmp_path / "multi-file-preview"))
    ctx = demo_admin_context()
    foundry.datasets.ensure("raw.orders", ctx=ctx, primary_key=["id"])
    committed = foundry.datasets.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)
    manifest_path = Path(committed.manifest_uri)
    manifest = foundry.datasets.inspect("raw.orders", ctx=ctx, version=committed.version_id)["manifest"]
    second_part = manifest_path.parent / "part-00001.parquet"
    unlisted_part = manifest_path.parent / "part-unlisted.parquet"
    foundry.compute_adapter.rows_to_parquet([{"id": "O-2", "amount": 200}], second_part, ["id", "amount"])
    foundry.compute_adapter.rows_to_parquet([{"id": "O-X", "amount": 999}], unlisted_part, ["id", "amount"])
    manifest["files"][0]["partition_values"] = {"bucket": "a"}
    manifest["files"].append(_manifest_file(second_part, partition_values={"bucket": "b"}))
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    original_preview: Callable[..., list[dict[str, object]]] = foundry.compute_adapter.preview_parquet
    read_paths: list[Path] = []

    def preview_spy(parquet_path: Path, *, limit: int) -> list[dict[str, object]]:
        read_paths.append(parquet_path)
        return original_preview(parquet_path, limit=limit)

    monkeypatch.setattr(foundry.compute_adapter, "preview_parquet", preview_spy)

    preview = foundry.datasets.preview("raw.orders", ctx=ctx, version=committed.version_id, limit=10)
    partition_preview = foundry.datasets.preview(
        "raw.orders",
        ctx=ctx,
        version=committed.version_id,
        limit=10,
        partition_filter={"bucket": "b"},
    )
    inspected = foundry.datasets.inspect("raw.orders", ctx=ctx, version=committed.version_id)

    assert [(row["id"], row["amount"]) for row in preview] == [("O-1", 100), ("O-2", 200)]
    assert [(row["id"], row["amount"]) for row in partition_preview] == [("O-2", 200)]
    assert read_paths == [Path(manifest["files"][0]["uri"]), second_part, second_part]
    assert inspected["manifest"]["files"][1]["partition_values"] == {"bucket": "b"}


def _csv_file(tmp_path: Path) -> Path:
    path = tmp_path / "orders.csv"
    path.write_text("id,amount\nO-1,100\n", encoding="utf-8")
    return path


def _duplicate_csv_file(tmp_path: Path) -> Path:
    path = tmp_path / "duplicate_orders.csv"
    path.write_text("id,amount\nO-2,200\nO-2,201\n", encoding="utf-8")
    return path


def _manifest_file(data_path: Path, *, partition_values: dict[str, object]) -> dict[str, object]:
    payload = data_path.read_bytes()
    return {
        "uri": str(data_path),
        "format": "parquet",
        "row_count": 1,
        "byte_size": len(payload),
        "content_hash": hashlib.sha256(payload).hexdigest(),
        "partition_values": partition_values,
    }
