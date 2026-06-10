from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class StoredDatasetCommit:
    """Storage result that application services persist as dataset version metadata."""

    manifest_uri: str
    data_file_uri: str
    data_file_path: Path
    byte_size: int
    content_hash: str
    manifest: dict[str, Any]


class DatasetStorageAdapter(Protocol):
    """Storage boundary for staged dataset files and committed dataset manifests."""

    profile_name: str

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

    def load_manifest(self, manifest_uri: str) -> dict[str, Any]:
        """Load a committed dataset manifest by logical URI."""
        ...

    def first_data_file_path(self, manifest_uri: str) -> Path:
        """Resolve the first data file in a manifest to a readable local path."""
        ...
