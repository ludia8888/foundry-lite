"""Application service helpers for transaction models workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from foundry_lite.application.ports import (
    DatasetCheckConfig,
    DatasetRow,
    DatasetStagedFile,
    DatasetTransactionMetadata,
    StoredDatasetCommit,
)
from foundry_lite.application.primitives import StagedFileStats


@dataclass(frozen=True)
class DatasetCommitTarget:
    row: DatasetRow
    dataset_id: str
    dataset_ref: str
    primary_key: list[str]

    @classmethod
    def from_row(cls, row: DatasetRow) -> DatasetCommitTarget:
        return cls(
            row=row,
            dataset_id=row["id"],
            dataset_ref=f"{row['namespace']}.{row['name']}",
            primary_key=list(row["primary_key"]),
        )


@dataclass(frozen=True)
class DatasetFinalizationRequest:
    target: DatasetCommitTarget
    transaction_id: str
    staged_parquet: Path
    staged_files: tuple[DatasetStagedFile, ...]
    run_id: str
    audit_action: str
    outbox_event_type: str
    extra_checks: list[DatasetCheckConfig]
    transaction_metadata: DatasetTransactionMetadata


@dataclass(frozen=True)
class DatasetFinalizationCheck:
    branch: str
    stats: StagedFileStats
    checked_manifest_hash: str
    staged_files_hash: str
    schema_version_id: str
    schema_version: int
    schema_evolution_metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class DatasetCommitArtifacts:
    version_id: str
    version_number: int
    schema_version: int
    stored_commit: StoredDatasetCommit
