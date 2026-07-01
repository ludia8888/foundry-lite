"""Application service helpers for transaction payloads workflows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from foundry_lite.application.ports import (
    DatasetCheckConfig,
    DatasetCheckResult,
    DatasetFileRecord,
    DatasetManifestFile,
    DatasetRow,
    DatasetStagedFile,
    DatasetStorageAdapter,
    DatasetTransactionMetadata,
    DatasetTransactionRepository,
    DatasetTransactionRow,
    StoredDatasetCommit,
    TransactionContext,
)
from foundry_lite.application.primitives import (
    CommitResult,
    StagedFileStats,
    _candidate_manifest_hash,
    _file_hash,
    _json_ready,
    _new_id,
    _now,
)
from foundry_lite.application.services.dataset.partition_warnings import partition_layout_warnings
from foundry_lite.application.services.dataset.protocols import DatasetRuntimeBoundary, DatasetVersionLookup
from foundry_lite.application.services.dataset.transaction_models import (
    DatasetCommitArtifacts,
    DatasetCommitTarget,
    DatasetFinalizationCheck,
    DatasetFinalizationRequest,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation


def single_file_finalization_request(
    dataset: DatasetRow,
    transaction_id: str,
    staged_parquet: Path,
    run_id: str,
    audit_action: str,
    outbox_event_type: str,
    extra_checks: Sequence[DatasetCheckConfig] | None,
    transaction_metadata: DatasetTransactionMetadata | None,
) -> DatasetFinalizationRequest:
    return DatasetFinalizationRequest(
        target=DatasetCommitTarget.from_row(dataset),
        transaction_id=transaction_id,
        staged_parquet=staged_parquet,
        staged_files=(DatasetStagedFile(path=staged_parquet),),
        run_id=run_id,
        audit_action=audit_action,
        outbox_event_type=outbox_event_type,
        extra_checks=list(extra_checks or []),
        transaction_metadata=dict(transaction_metadata or {}),
    )


def multi_file_finalization_request(
    dataset: DatasetRow,
    transaction_id: str,
    validation_parquet: Path,
    staged_files: Sequence[DatasetStagedFile],
    run_id: str,
    audit_action: str,
    outbox_event_type: str,
    transaction_metadata: DatasetTransactionMetadata | None,
) -> DatasetFinalizationRequest:
    return DatasetFinalizationRequest(
        target=DatasetCommitTarget.from_row(dataset),
        transaction_id=transaction_id,
        staged_parquet=validation_parquet,
        staged_files=tuple(staged_files),
        run_id=run_id,
        audit_action=audit_action,
        outbox_event_type=outbox_event_type,
        extra_checks=[],
        transaction_metadata=dict(transaction_metadata or {}),
    )


def build_dataset_file_record(
    ctx: RequestContext,
    validation: DatasetFinalizationCheck,
    commit: DatasetCommitArtifacts,
) -> DatasetFileRecord:
    del validation
    return build_dataset_file_records(ctx, commit)[0]


def build_dataset_file_records(
    ctx: RequestContext,
    commit: DatasetCommitArtifacts,
) -> list[DatasetFileRecord]:
    return [_dataset_file_record(ctx, commit.version_id, manifest_file) for manifest_file in _manifest_files(commit)]


def _dataset_file_record(
    ctx: RequestContext,
    version_id: str,
    manifest_file: DatasetManifestFile,
) -> DatasetFileRecord:
    return DatasetFileRecord(
        file_id=_new_id("dsf"),
        tenant_id=ctx.tenant_id,
        dataset_version_id=version_id,
        uri=manifest_file["uri"],
        file_format=manifest_file["format"],
        row_count=manifest_file["row_count"],
        byte_size=manifest_file["byte_size"],
        content_hash=manifest_file["content_hash"],
        partition_values=manifest_file.get("partition_values") or {},
    )


def _manifest_files(commit: DatasetCommitArtifacts) -> list[DatasetManifestFile]:
    files = list(commit.stored_commit.manifest.get("files") or [])
    if files:
        return files
    return [
        {
            "uri": commit.stored_commit.data_file_uri,
            "format": "parquet",
            "row_count": 0,
            "byte_size": commit.stored_commit.byte_size,
            "content_hash": commit.stored_commit.content_hash,
        }
    ]


def build_commit_result(
    request: DatasetFinalizationRequest,
    validation: DatasetFinalizationCheck,
    commit: DatasetCommitArtifacts,
) -> CommitResult:
    return CommitResult(
        dataset_id=request.target.dataset_id,
        dataset_ref=request.target.dataset_ref,
        transaction_id=request.transaction_id,
        version_id=commit.version_id,
        version_number=commit.version_number,
        row_count=validation.stats.row_count,
        manifest_uri=commit.stored_commit.manifest_uri,
        schema_hash=validation.stats.schema_hash,
    )


def finalized_transaction_metadata(
    request: DatasetFinalizationRequest,
    validation: DatasetFinalizationCheck,
) -> DatasetTransactionMetadata:
    metadata: dict[str, object] = dict(request.transaction_metadata)
    if warnings := partition_layout_warnings(request, validation):
        metadata["partitionLayoutWarnings"] = warnings
    if validation.schema_evolution_metadata is not None:
        metadata["schemaEvolution"] = validation.schema_evolution_metadata
    return metadata


def commit_staged_dataset_files(
    storage: DatasetStorageAdapter,
    *,
    tenant_id: str,
    dataset_id: str,
    branch: str,
    version_id: str,
    dataset_ref: str,
    schema_hash: str,
    staged_files: tuple[DatasetStagedFile, ...],
    row_count: int,
    created_at: str,
    sort_order: Sequence[str] | None,
) -> StoredDatasetCommit:
    if len(staged_files) == 1 and not staged_files[0].partition_values:
        return storage.commit_staged_file(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            branch=branch,
            version_id=version_id,
            dataset_ref=dataset_ref,
            schema_hash=schema_hash,
            staged_file=staged_files[0].path,
            row_count=row_count,
            created_at=created_at,
            sort_order=sort_order,
        )
    return storage.commit_staged_files(
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        branch=branch,
        version_id=version_id,
        dataset_ref=dataset_ref,
        schema_hash=schema_hash,
        staged_files=staged_files,
        row_count=row_count,
        created_at=created_at,
        sort_order=sort_order,
    )


def cleanup_committed_version_artifacts(
    storage: DatasetStorageAdapter,
    ctx: RequestContext,
    request: DatasetFinalizationRequest,
    validation: DatasetFinalizationCheck,
    commit: DatasetCommitArtifacts,
) -> dict[str, object]:
    removed = storage.delete_committed_version(
        tenant_id=ctx.tenant_id,
        dataset_id=request.target.dataset_id,
        branch=validation.branch,
        version_id=commit.version_id,
    )
    return {
        "version_id": commit.version_id,
        "manifest_uri": commit.stored_commit.manifest_uri,
        "removed": removed,
    }


def emit_dataset_commit_events(
    runtime_service: DatasetRuntimeBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    request: DatasetFinalizationRequest,
    validation: DatasetFinalizationCheck,
    commit: DatasetCommitArtifacts,
) -> None:
    runtime_service._outbox(
        conn,
        ctx,
        request.outbox_event_type,
        "dataset_version",
        commit.version_id,
        {
            "datasetId": request.target.dataset_id,
            "datasetRef": request.target.dataset_ref,
            "versionId": commit.version_id,
            "branch": validation.branch,
        },
        idempotency_key=commit.version_id,
        correlation_id=request.run_id,
    )
    runtime_service._audit(
        conn,
        ctx,
        event_type="dataset.version.committed",
        resource_type="dataset_version",
        resource_id=commit.version_id,
        action=request.audit_action,
        after_ref=dataset_commit_after_ref(request, validation, commit),
        correlation_id=request.run_id,
    )


def ensure_snapshot_base_current(
    *,
    conn: TransactionContext,
    tx: DatasetTransactionRow,
    dataset_version_service: DatasetVersionLookup,
) -> None:
    if tx["tx_type"] != "SNAPSHOT":
        return
    latest = dataset_version_service._latest_version_by_dataset_id(conn, tx["dataset_id"], allow_missing=True)
    current_base_version_id = latest["id"] if latest else None
    if current_base_version_id == tx["base_version_id"]:
        return
    raise ConflictDetected(
        "dataset transaction base version is stale",
        details={
            "transaction_id": tx["id"],
            "dataset_id": tx["dataset_id"],
            "expected_base_version_id": tx["base_version_id"],
            "current_base_version_id": current_base_version_id,
        },
    )


def commit_open_dataset_transaction(
    *,
    repository: DatasetTransactionRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    transaction_id: str,
    version_id: str,
    schema_version: int,
    metadata: DatasetTransactionMetadata,
) -> None:
    committed = repository.commit_transaction(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        transaction_id=transaction_id,
        committed_version_id=version_id,
        schema_version=schema_version,
        committed_at=_now(),
        metadata=metadata,
    )
    if committed:
        return
    raise ConflictDetected(
        "dataset transaction commit lost its OPEN state",
        details={"transaction_id": transaction_id, "version_id": version_id},
    )


def dataset_commit_after_ref(
    request: DatasetFinalizationRequest,
    validation: DatasetFinalizationCheck,
    commit: DatasetCommitArtifacts,
) -> dict[str, object]:
    after_ref: dict[str, object] = {
        "dataset_ref": request.target.dataset_ref,
        "version_number": commit.version_number,
        "validated_against_schema_version_id": validation.schema_version_id,
        "validated_against_schema_version": validation.schema_version,
    }
    if validation.schema_evolution_metadata is not None:
        after_ref["schema_evolution"] = validation.schema_evolution_metadata
    return after_ref


def dataset_version_conflict_error(
    request: DatasetFinalizationRequest,
    validation: DatasetFinalizationCheck,
    commit: DatasetCommitArtifacts,
    cleanup: dict[str, object],
) -> ConflictDetected:
    return ConflictDetected(
        "dataset version allocation conflict",
        details={
            "dataset_id": request.target.dataset_id,
            "transaction_id": request.transaction_id,
            "branch": validation.branch,
            "version_number": commit.version_number,
            "orphan_cleanup": cleanup,
        },
    )


def dataset_commit_persistence_error(
    request: DatasetFinalizationRequest,
    validation: DatasetFinalizationCheck,
    commit: DatasetCommitArtifacts,
    cleanup: dict[str, object],
) -> InvariantViolation:
    return InvariantViolation(
        "dataset commit metadata persistence failed",
        details={
            "dataset_id": request.target.dataset_id,
            "transaction_id": request.transaction_id,
            "branch": validation.branch,
            "version_number": commit.version_number,
            "orphan_cleanup": cleanup,
        },
    )


def checked_manifest_hash(stats: StagedFileStats) -> str:
    return _candidate_manifest_hash(
        row_count=stats.row_count,
        byte_size=stats.byte_size,
        content_hash=stats.content_hash,
        schema_hash=stats.schema_hash,
    )


def current_candidate_manifest_hash(staged_parquet: Path, validation: DatasetFinalizationCheck) -> str:
    return _candidate_manifest_hash(
        row_count=validation.stats.row_count,
        byte_size=staged_parquet.stat().st_size,
        content_hash=_file_hash(staged_parquet),
        schema_hash=validation.stats.schema_hash,
    )


def current_staged_files_hash(staged_files: tuple[DatasetStagedFile, ...]) -> str:
    digest = hashlib.sha256()
    for index, staged_file in enumerate(staged_files):
        payload = {
            "index": index,
            "byte_size": staged_file.path.stat().st_size,
            "content_hash": _file_hash(staged_file.path),
            "partition_values": _json_ready(dict(staged_file.partition_values or {})),
            "row_count": staged_file.row_count,
        }
        digest.update(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return digest.hexdigest()


def finalization_candidate_unchanged(
    request: DatasetFinalizationRequest,
    validation: DatasetFinalizationCheck,
) -> bool:
    return (
        current_candidate_manifest_hash(request.staged_parquet, validation) == validation.checked_manifest_hash
        and current_staged_files_hash(request.staged_files) == validation.staged_files_hash
    )


def block_commit_failure(failure: DatasetCheckResult) -> DatasetCheckResult:
    return {**failure, "contract_status": "BLOCK_COMMIT"}
