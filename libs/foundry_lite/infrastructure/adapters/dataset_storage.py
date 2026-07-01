"""Infrastructure adapter implementation for dataset storage."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from foundry_lite.application.ports import (
    DatasetManifest,
    DatasetManifestFile,
    DatasetStagedFile,
    StoredDatasetCommit,
)
from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.primitives import _file_hash
from foundry_lite.infrastructure.adapters.dataset_manifest_metadata import parquet_manifest_file_metadata


class LocalDatasetStorageAdapter:
    """Filesystem-backed dataset storage adapter for the local MVP runtime."""

    profile_name = "local"
    uri_scheme: str | None = None

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "commit_staged_file",
                    "unavailable",
                    True,
                    "Dataset artifact storage is unavailable; retry the same version promotion.",
                ),
                AdapterFailureMode(
                    "load_manifest",
                    "not_found",
                    False,
                    "Dataset manifest is missing; inspect storage consistency before retrying.",
                ),
                AdapterFailureMode(
                    "delete_committed_version",
                    "unavailable",
                    True,
                    "Committed artifact cleanup could not reach storage; retry cleanup.",
                ),
                AdapterFailureMode(
                    "delete_staging_transaction",
                    "unavailable",
                    True,
                    "Staging cleanup could not reach storage; retry cleanup or inspect the transaction.",
                ),
            ),
        )

    def dataset_uri(self, tenant_id: str, dataset_id: str) -> str:
        return self._uri_for(self._dataset_dir(tenant_id, dataset_id))

    def staging_file(self, *, tenant_id: str, dataset_id: str, transaction_id: str, file_name: str) -> Path:
        path = self._staging_dir(tenant_id, dataset_id, transaction_id) / file_name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

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
        sort_order: Sequence[str] | None = None,
    ) -> StoredDatasetCommit:
        return self.commit_staged_files(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            branch=branch,
            version_id=version_id,
            dataset_ref=dataset_ref,
            schema_hash=schema_hash,
            staged_files=(DatasetStagedFile(path=staged_file, row_count=row_count),),
            row_count=row_count,
            created_at=created_at,
            sort_order=sort_order,
        )

    def commit_staged_files(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        branch: str,
        version_id: str,
        dataset_ref: str,
        schema_hash: str,
        staged_files: Sequence[DatasetStagedFile],
        row_count: int,
        created_at: str,
        sort_order: Sequence[str] | None = None,
    ) -> StoredDatasetCommit:
        if not staged_files:
            raise ValueError("dataset commit requires at least one staged file")
        version_dir = self._version_dir(tenant_id, dataset_id, branch, version_id)
        self._prepare_version_dir(version_dir)
        version_dir.mkdir(parents=True, exist_ok=True)
        manifest_files = self._promote_manifest_files(version_dir, staged_files, row_count, sort_order)
        manifest_path = version_dir / "manifest.json"
        manifest: DatasetManifest = {
            "version_id": version_id,
            "dataset": dataset_ref,
            "branch": branch,
            "schema_hash": schema_hash,
            "files": manifest_files,
            "created_at": created_at,
            "storage_profile": self.profile_name,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        self._verify_committed_manifest_files(manifest_path, manifest_files)
        first_file = manifest_files[0]
        first_path = self._path_for(first_file["uri"])
        return StoredDatasetCommit(
            manifest_uri=self._uri_for(manifest_path),
            data_file_uri=first_file["uri"],
            data_file_path=first_path,
            byte_size=sum(file["byte_size"] for file in manifest_files),
            content_hash=_combined_content_hash(manifest_files),
            manifest=manifest,
        )

    def delete_committed_version(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        branch: str,
        version_id: str,
    ) -> bool:
        version_dir = self._version_dir(tenant_id, dataset_id, branch, version_id)
        if not version_dir.exists():
            return False
        shutil.rmtree(version_dir)
        return True

    def delete_staging_transaction(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        transaction_id: str,
    ) -> bool:
        staging_dir = self._staging_dir(tenant_id, dataset_id, transaction_id)
        if not staging_dir.exists():
            return False
        shutil.rmtree(staging_dir)
        return True

    def load_manifest(self, manifest_uri: str) -> DatasetManifest:
        return cast(DatasetManifest, json.loads(self._path_for(manifest_uri).read_text(encoding="utf-8")))

    def first_data_file_path(self, manifest_uri: str) -> Path:
        return self.data_file_paths(manifest_uri)[0]

    def data_file_paths(
        self,
        manifest_uri: str,
        *,
        partition_filter: Mapping[str, object] | None = None,
    ) -> list[Path]:
        manifest = self.load_manifest(manifest_uri)
        files = manifest.get("files") or []
        if not files:
            raise ValueError(f"dataset manifest has no files: {manifest_uri}")
        return [self._data_file_path(file) for file in _matching_manifest_files(manifest, partition_filter)]

    def preview_file_paths(
        self,
        manifest_uri: str,
        *,
        partition_filter: Mapping[str, object] | None = None,
    ) -> list[Path]:
        return self.data_file_paths(manifest_uri, partition_filter=partition_filter)

    def _data_file_path(self, manifest_file: DatasetManifestFile) -> Path:
        path = self._path_for(manifest_file["uri"])
        if not path.exists():
            raise FileNotFoundError(str(path))
        return path

    def _verify_committed_manifest(
        self,
        manifest_path: Path,
        data_path: Path,
        manifest_file: DatasetManifestFile,
    ) -> None:
        manifest = self.load_manifest(self._uri_for(manifest_path))
        files = manifest.get("files") or []
        if not files or files[0]["uri"] != self._uri_for(data_path):
            raise ValueError("dataset manifest does not reference the committed data file")
        self._verify_committed_manifest_files(manifest_path, [manifest_file])

    def _verify_committed_manifest_files(
        self,
        manifest_path: Path,
        manifest_files: Sequence[DatasetManifestFile],
    ) -> None:
        manifest = self.load_manifest(self._uri_for(manifest_path))
        files = manifest.get("files") or []
        if [file["uri"] for file in files] != [file["uri"] for file in manifest_files]:
            raise ValueError("dataset manifest does not reference the committed data files")
        for manifest_file in manifest_files:
            data_path = self._path_for(manifest_file["uri"])
            if data_path.stat().st_size != manifest_file["byte_size"]:
                raise ValueError("dataset manifest byte size does not match committed data file")
            if _file_hash(data_path) != manifest_file["content_hash"]:
                raise ValueError("dataset manifest content hash does not match committed data file")

    def _promote_manifest_files(
        self,
        version_dir: Path,
        staged_files: Sequence[DatasetStagedFile],
        row_count: int,
        sort_order: Sequence[str] | None,
    ) -> list[DatasetManifestFile]:
        if _staged_files_row_count(staged_files, row_count) != row_count:
            raise ValueError("dataset staged file row counts do not match validation row count")
        manifest_files: list[DatasetManifestFile] = []
        for index, staged_file in enumerate(staged_files):
            final_parquet = version_dir / f"part-{index:05d}.parquet"
            shutil.copy2(staged_file.path, final_parquet)
            manifest_files.append(self._manifest_file(final_parquet, staged_file, row_count, sort_order))
        return manifest_files

    def _manifest_file(
        self,
        final_parquet: Path,
        staged_file: DatasetStagedFile,
        row_count: int,
        sort_order: Sequence[str] | None,
    ) -> DatasetManifestFile:
        manifest_file: DatasetManifestFile = {
            "uri": self._uri_for(final_parquet),
            "format": "parquet",
            "row_count": _part_row_count(staged_file, row_count),
            "byte_size": final_parquet.stat().st_size,
            "content_hash": _file_hash(final_parquet),
            "partition_values": dict(staged_file.partition_values or {}),
        }
        return parquet_manifest_file_metadata(manifest_file, final_parquet, sort_order=sort_order)

    def _dataset_dir(self, tenant_id: str, dataset_id: str) -> Path:
        return self.root / tenant_id / "datasets" / dataset_id

    def _prepare_version_dir(self, version_dir: Path) -> None:
        manifest_path = version_dir / "manifest.json"
        if manifest_path.exists():
            raise FileExistsError(f"dataset version already committed: {version_dir}")
        if version_dir.exists():
            shutil.rmtree(version_dir)

    def _version_dir(self, tenant_id: str, dataset_id: str, branch: str, version_id: str) -> Path:
        return self._dataset_dir(tenant_id, dataset_id) / f"branch={branch}" / f"version={version_id}"

    def _staging_dir(self, tenant_id: str, dataset_id: str, transaction_id: str) -> Path:
        return self._dataset_dir(tenant_id, dataset_id) / "_staging" / transaction_id

    def _uri_for(self, path: Path) -> str:
        if self.uri_scheme is None:
            return str(path)
        relative = path.resolve().relative_to(self.root).as_posix()
        return f"{self.uri_scheme}://{relative}"

    def _path_for(self, uri: str) -> Path:
        if self.uri_scheme is not None and uri.startswith(f"{self.uri_scheme}://"):
            relative = uri.removeprefix(f"{self.uri_scheme}://")
            return self.root / relative
        return Path(uri)


class FakeDatasetStorageAdapter(LocalDatasetStorageAdapter):
    """S3-like fake profile that keeps files local but exposes logical non-file URIs."""

    profile_name = "fake-storage"
    uri_scheme = "fake-storage"


def _matching_manifest_files(
    manifest: DatasetManifest,
    partition_filter: Mapping[str, object] | None,
) -> list[DatasetManifestFile]:
    files = manifest.get("files") or []
    if partition_filter is None:
        return files
    return [file for file in files if _matches_partition_filter(file, partition_filter)]


def _matches_partition_filter(
    manifest_file: DatasetManifestFile,
    partition_filter: Mapping[str, object],
) -> bool:
    partition_values = manifest_file.get("partition_values") or {}
    return all(partition_values.get(key) == value for key, value in partition_filter.items())


def _part_row_count(staged_file: DatasetStagedFile, total_row_count: int) -> int:
    return total_row_count if staged_file.row_count is None else staged_file.row_count


def _staged_files_row_count(staged_files: Sequence[DatasetStagedFile], total_row_count: int) -> int:
    return sum(_part_row_count(staged_file, total_row_count) for staged_file in staged_files)


def _combined_content_hash(files: Sequence[DatasetManifestFile]) -> str:
    if len(files) == 1:
        return files[0]["content_hash"]
    digest = json.dumps([file["content_hash"] for file in files], sort_keys=True).encode("utf-8")
    return _file_hash_bytes(digest)


def _file_hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
