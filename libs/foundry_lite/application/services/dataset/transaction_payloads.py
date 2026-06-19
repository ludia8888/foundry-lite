from __future__ import annotations

from pathlib import Path

from foundry_lite.application.ports import DatasetCheckResult, DatasetFileRecord, DatasetTransactionMetadata
from foundry_lite.application.primitives import (
    CommitResult,
    StagedFileStats,
    _candidate_manifest_hash,
    _file_hash,
    _new_id,
)
from foundry_lite.application.services.dataset.transaction_models import (
    DatasetCommitArtifacts,
    DatasetFinalizationCheck,
    DatasetFinalizationRequest,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation


def build_dataset_file_record(
    ctx: RequestContext,
    validation: DatasetFinalizationCheck,
    commit: DatasetCommitArtifacts,
) -> DatasetFileRecord:
    return DatasetFileRecord(
        file_id=_new_id("dsf"),
        tenant_id=ctx.tenant_id,
        dataset_version_id=commit.version_id,
        uri=commit.stored_commit.data_file_uri,
        file_format="parquet",
        row_count=validation.stats.row_count,
        byte_size=commit.stored_commit.byte_size,
        content_hash=commit.stored_commit.content_hash,
        partition_values={},
    )


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
    if validation.schema_evolution_metadata is not None:
        metadata["schemaEvolution"] = validation.schema_evolution_metadata
    return metadata


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


def block_commit_failure(failure: DatasetCheckResult) -> DatasetCheckResult:
    return {**failure, "contract_status": "BLOCK_COMMIT"}
