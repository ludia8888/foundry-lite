from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import cast

from foundry_lite.application.ports import DatasetManifest, DatasetManifestFile, StoredDatasetCommit
from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.primitives import _file_hash


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
            ),
        )

    def dataset_uri(self, tenant_id: str, dataset_id: str) -> str:
        return self._uri_for(self._dataset_dir(tenant_id, dataset_id))

    def staging_file(self, *, tenant_id: str, dataset_id: str, transaction_id: str, file_name: str) -> Path:
        path = self._dataset_dir(tenant_id, dataset_id) / "_staging" / transaction_id / file_name
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
    ) -> StoredDatasetCommit:
        version_dir = self._version_dir(tenant_id, dataset_id, branch, version_id)
        version_dir.mkdir(parents=True, exist_ok=True)
        final_parquet = version_dir / "part-00000.parquet"
        shutil.copy2(staged_file, final_parquet)

        data_file_uri = self._uri_for(final_parquet)
        manifest_path = version_dir / "manifest.json"
        manifest_file: DatasetManifestFile = {
            "uri": data_file_uri,
            "format": "parquet",
            "row_count": row_count,
            "byte_size": final_parquet.stat().st_size,
            "content_hash": _file_hash(final_parquet),
        }
        manifest: DatasetManifest = {
            "version_id": version_id,
            "dataset": dataset_ref,
            "branch": branch,
            "schema_hash": schema_hash,
            "files": [manifest_file],
            "created_at": created_at,
            "storage_profile": self.profile_name,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        return StoredDatasetCommit(
            manifest_uri=self._uri_for(manifest_path),
            data_file_uri=data_file_uri,
            data_file_path=final_parquet,
            byte_size=final_parquet.stat().st_size,
            content_hash=_file_hash(final_parquet),
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

    def load_manifest(self, manifest_uri: str) -> DatasetManifest:
        return cast(DatasetManifest, json.loads(self._path_for(manifest_uri).read_text(encoding="utf-8")))

    def first_data_file_path(self, manifest_uri: str) -> Path:
        manifest = self.load_manifest(manifest_uri)
        return self._path_for(manifest["files"][0]["uri"])

    def _dataset_dir(self, tenant_id: str, dataset_id: str) -> Path:
        return self.root / tenant_id / "datasets" / dataset_id

    def _version_dir(self, tenant_id: str, dataset_id: str, branch: str, version_id: str) -> Path:
        return self._dataset_dir(tenant_id, dataset_id) / f"branch={branch}" / f"version={version_id}"

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
