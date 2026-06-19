from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import NotRequired, Protocol, TypedDict

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.dataset_quality_repository import DatasetSchemaJson
from foundry_lite.application.ports.dataset_version_repository import DatasetVersionRow


class DatasetManifestFile(TypedDict):
    uri: str
    format: str
    row_count: int
    byte_size: int
    content_hash: str
    partition_values: NotRequired[dict[str, object]]


class DatasetManifest(TypedDict):
    version_id: str
    dataset: str
    branch: str
    schema_hash: str
    files: list[DatasetManifestFile]
    created_at: str
    storage_profile: str


class DatasetInspectionPayload(TypedDict):
    dataset: str
    dataset_id: str
    version: DatasetVersionRow
    schema: DatasetSchemaJson
    manifest: DatasetManifest


@dataclass(frozen=True)
class StoredDatasetCommit:
    """Storage result that application services persist as dataset version metadata."""

    manifest_uri: str
    data_file_uri: str
    data_file_path: Path
    byte_size: int
    content_hash: str
    manifest: DatasetManifest


class DatasetStorageAdapter(Protocol):
    """Storage boundary for staged dataset files and committed dataset manifests."""

    @property
    def profile_name(self) -> str: ...

    def failure_contract(self) -> AdapterFailureContract:
        """Return the adapter failure taxonomy promised by this profile."""
        ...

    def dataset_uri(self, tenant_id: str, dataset_id: str) -> str:
        """Return the logical storage URI for a dataset."""
        ...

    def staging_file(self, *, tenant_id: str, dataset_id: str, transaction_id: str, file_name: str) -> Path:
        """Return a writable local staging path for one transaction file."""
        ...

    def commit_staged_file(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        branch: str,
        version_id: str,
        dataset_ref: str,
        schema_hash: str,
        staged_file: Path,
        row_count: int,
        created_at: str,
    ) -> StoredDatasetCommit:
        """Promote a staged file into a committed dataset version manifest."""
        ...

    def delete_committed_version(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        branch: str,
        version_id: str,
    ) -> bool:
        """Remove committed version artifacts after metadata persistence fails."""
        ...

    def delete_staging_transaction(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        transaction_id: str,
    ) -> bool:
        """Remove uncommitted staging artifacts after a transaction aborts."""
        ...

    def load_manifest(self, manifest_uri: str) -> DatasetManifest:
        """Load a committed dataset manifest by logical URI."""
        ...

    def first_data_file_path(self, manifest_uri: str) -> Path:
        """Resolve the first data file in a manifest to a readable local path."""
        ...

    def data_file_paths(
        self,
        manifest_uri: str,
        *,
        partition_filter: Mapping[str, object] | None = None,
    ) -> list[Path]:
        """Resolve all manifest data files to readable local paths in manifest order."""
        ...
