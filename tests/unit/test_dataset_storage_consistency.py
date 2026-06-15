from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from foundry_lite.application.core import FoundryLiteCore
from foundry_lite.application.ports import DatasetFileRecord
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import InvariantViolation, ValidationFailed
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies
from foundry_lite.infrastructure.repositories import SqlAlchemyDatasetTransactionRepository


class _FailingFileInsertRepository:
    def __init__(self, wrapped: SqlAlchemyDatasetTransactionRepository) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> object:
        return getattr(self._wrapped, name)

    def insert_file(self, *, transaction: object, record: DatasetFileRecord) -> None:
        del transaction, record
        raise RuntimeError("dataset file insert exploded after storage promotion")


def test_dataset_commit_storage_success_db_failure_creates_orphan_cleanup_evidence(tmp_path: Path) -> None:
    dependencies = create_local_core_dependencies(storage_root=tmp_path / "split-brain-runtime")
    failing = _FailingFileInsertRepository(dependencies.dataset_transaction_repository)
    core = FoundryLiteCore(dependencies=replace(dependencies, dataset_transaction_repository=failing))
    ctx = demo_admin_context()
    core.ensure_dataset("raw.orders", ctx=ctx, primary_key=["id"])

    with pytest.raises(InvariantViolation) as exc_info:
        core.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)

    cleanup = exc_info.value.details["orphan_cleanup"]
    assert cleanup["removed"] is True
    assert not list(dependencies.storage_root.glob("**/version=*"))
    failed_run = next(run for run in core.list_runs(ctx=ctx)["syncRuns"] if run["status"] == "FAILED")
    error = failed_run["error"]
    assert error["type"] == "INVARIANT_VIOLATION"
    assert error["details"]["orphan_cleanup"]["manifest_uri"] == cleanup["manifest_uri"]


def test_dataset_commit_db_success_manifest_missing_marks_storage_corruption(tmp_path: Path) -> None:
    core = FoundryLiteCore(dependencies=create_local_core_dependencies(storage_root=tmp_path / "missing-manifest"))
    ctx = demo_admin_context()
    core.ensure_dataset("raw.orders", ctx=ctx, primary_key=["id"])
    committed = core.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)
    Path(committed.manifest_uri).unlink()

    with pytest.raises(InvariantViolation) as exc_info:
        core.inspect_dataset("raw.orders", ctx=ctx, version=committed.version_id)

    details = exc_info.value.details
    assert details["error_type"] == "committed_version_storage_missing"
    assert details["dataset_ref"] == "raw.orders"
    assert details["version_id"] == committed.version_id
    assert details["manifest_uri"] == committed.manifest_uri


def test_dataset_preview_data_file_missing_marks_storage_corruption(tmp_path: Path) -> None:
    core = FoundryLiteCore(dependencies=create_local_core_dependencies(storage_root=tmp_path / "missing-data-file"))
    ctx = demo_admin_context()
    core.ensure_dataset("raw.orders", ctx=ctx, primary_key=["id"])
    committed = core.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)
    manifest = core.inspect_dataset("raw.orders", ctx=ctx, version=committed.version_id)["manifest"]
    Path(manifest["files"][0]["uri"]).unlink()

    with pytest.raises(InvariantViolation) as exc_info:
        core.preview_dataset("raw.orders", ctx=ctx, version=committed.version_id)

    details = exc_info.value.details
    assert details["error_type"] == "committed_version_storage_missing"
    assert details["dataset_id"]
    assert details["version_id"] == committed.version_id
    assert details["manifest_uri"] == committed.manifest_uri


def test_abort_cleanup_never_deletes_committed_manifest(tmp_path: Path) -> None:
    core = FoundryLiteCore(dependencies=create_local_core_dependencies(storage_root=tmp_path / "abort-cleanup"))
    ctx = demo_admin_context()
    core.ensure_dataset("raw.orders", ctx=ctx, primary_key=["id"])
    committed = core.upload_csv("raw.orders", _csv_file(tmp_path), ctx=ctx)
    manifest_before = core.inspect_dataset("raw.orders", ctx=ctx, version=committed.version_id)["manifest"]

    with pytest.raises(ValidationFailed, match="dataset checks failed"):
        core.upload_csv("raw.orders", _duplicate_csv_file(tmp_path), ctx=ctx)

    manifest_after = core.inspect_dataset("raw.orders", ctx=ctx, version=committed.version_id)["manifest"]
    preview = core.preview_dataset("raw.orders", ctx=ctx, version=committed.version_id)
    assert Path(committed.manifest_uri).exists()
    assert manifest_after["files"] == manifest_before["files"]
    assert [(row["id"], row["amount"]) for row in preview] == [("O-1", 100)]


def _csv_file(tmp_path: Path) -> Path:
    path = tmp_path / "orders.csv"
    path.write_text("id,amount\nO-1,100\n", encoding="utf-8")
    return path


def _duplicate_csv_file(tmp_path: Path) -> Path:
    path = tmp_path / "duplicate_orders.csv"
    path.write_text("id,amount\nO-2,200\nO-2,201\n", encoding="utf-8")
    return path
