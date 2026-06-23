from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from foundry_lite.application.ports import DatasetManifest, DatasetManifestFile, StoredDatasetCommit
from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureKind,
    AdapterFailureMode,
)
from foundry_lite.application.primitives import _file_hash
from foundry_lite.infrastructure.adapters.s3_client import build_s3_client, is_not_found_error, join_key


@dataclass(frozen=True)
class S3DatasetStorageAdapterConfig:
    bucket: str
    endpoint_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = field(default=None, repr=False)
    region_name: str = "us-east-1"
    prefix: str = "foundry-lite"
    cache_root: Path = Path(".foundry-lite/s3-cache")
    should_create_bucket_if_missing: bool = True


class S3DatasetStorageAdapter:
    """S3-compatible dataset storage adapter, tested against MinIO before AWS."""

    profile_name = "s3-storage"

    def __init__(self, config: S3DatasetStorageAdapterConfig, *, client: Any | None = None) -> None:
        self.config = config
        self.cache_root = Path(config.cache_root).resolve()
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._client = client or _boto3_s3_client(config)
        if config.should_create_bucket_if_missing:
            self._ensure_bucket()

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "commit_staged_file",
                    "timeout",
                    True,
                    "S3 object promotion outcome is unknown; retry with the same version id after cleanup check.",
                    timeout_seconds=30,
                    has_required_idempotency_key=True,
                ),
                AdapterFailureMode(
                    "commit_staged_file",
                    "conflict",
                    False,
                    "S3 dataset version id is already committed; the version allocator must hand out a fresh id.",
                ),
                AdapterFailureMode(
                    "load_manifest",
                    "not_found",
                    False,
                    "S3 dataset manifest is missing; treat the committed dataset version as storage-corrupt.",
                ),
                AdapterFailureMode(
                    "load_manifest",
                    "unavailable",
                    True,
                    "S3 read could not reach storage; this is transient, not corruption — retry the read.",
                ),
                AdapterFailureMode(
                    "delete_committed_version",
                    "unavailable",
                    True,
                    "S3 orphan cleanup could not reach storage; retry cleanup before advancing the ratchet.",
                ),
            ),
        )

    def dataset_uri(self, tenant_id: str, dataset_id: str) -> str:
        return self._uri_for_key(self._dataset_prefix(tenant_id, dataset_id))

    def staging_file(self, *, tenant_id: str, dataset_id: str, transaction_id: str, file_name: str) -> Path:
        path = self.cache_root / "staging" / tenant_id / dataset_id / transaction_id / file_name
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
        version_prefix = self._version_prefix(tenant_id, dataset_id, branch, version_id)
        data_key = f"{version_prefix}/part-00000.parquet"
        manifest_key = f"{version_prefix}/manifest.json"
        # Backstop against accidental version-id reuse. The transactional version
        # allocator is the real uniqueness guarantee; this only stops a duplicate
        # commit from overwriting or, worse, deleting an already-committed version.
        self._guard_version_not_committed(manifest_key, version_id)
        try:
            return self._commit_objects(
                data_key, manifest_key, version_id, dataset_ref, branch, schema_hash, staged_file, row_count, created_at
            )
        except AdapterError as exc:
            # Never run the destructive cleanup when the failure is a duplicate-version
            # conflict: the existing objects belong to a healthy prior commit.
            if exc.failure.kind != "conflict":
                self._safe_cleanup_failed_commit(data_key, manifest_key)
            raise
        except Exception as exc:
            cleanup = self._safe_cleanup_failed_commit(data_key, manifest_key)
            raise self._adapter_error("commit_staged_file", exc, idempotency_key=version_id, details=cleanup) from exc

    def delete_committed_version(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        branch: str,
        version_id: str,
    ) -> bool:
        return self._delete_prefix(self._version_prefix(tenant_id, dataset_id, branch, version_id))

    def delete_staging_transaction(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        transaction_id: str,
    ) -> bool:
        staging_dir = self.cache_root / "staging" / tenant_id / dataset_id / transaction_id
        if not staging_dir.exists():
            return False
        shutil.rmtree(staging_dir)
        return True

    def load_manifest(self, manifest_uri: str) -> DatasetManifest:
        key = self._key_for_uri(manifest_uri)
        body = self._get_object_bytes(key)
        return cast(DatasetManifest, json.loads(body.decode("utf-8")))

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
        return [
            self._download_manifest_file(file)
            for file in files
            if partition_filter is None or _matches_partition_filter(file, partition_filter)
        ]

    def preview_file_paths(
        self,
        manifest_uri: str,
        *,
        partition_filter: Mapping[str, object] | None = None,
    ) -> list[Path]:
        return self.data_file_paths(manifest_uri, partition_filter=partition_filter)

    def _commit_objects(
        self,
        data_key: str,
        manifest_key: str,
        version_id: str,
        dataset_ref: str,
        branch: str,
        schema_hash: str,
        staged_file: Path,
        row_count: int,
        created_at: str,
    ) -> StoredDatasetCommit:
        self._client.upload_file(str(staged_file), self.config.bucket, data_key)
        manifest_file = self._manifest_file(data_key, staged_file, row_count)
        manifest = self._manifest(version_id, dataset_ref, branch, schema_hash, manifest_file, created_at)
        self._put_json(manifest_key, manifest)
        self._verify_committed_manifest(manifest_key, manifest_file)
        return StoredDatasetCommit(
            manifest_uri=self._uri_for_key(manifest_key),
            data_file_uri=manifest_file["uri"],
            data_file_path=self._download_manifest_file(manifest_file),
            byte_size=manifest_file["byte_size"],
            content_hash=manifest_file["content_hash"],
            manifest=manifest,
        )

    def _manifest_file(self, data_key: str, staged_file: Path, row_count: int) -> DatasetManifestFile:
        return {
            "uri": self._uri_for_key(data_key),
            "format": "parquet",
            "row_count": row_count,
            "byte_size": staged_file.stat().st_size,
            "content_hash": _file_hash(staged_file),
        }

    def _manifest(
        self,
        version_id: str,
        dataset_ref: str,
        branch: str,
        schema_hash: str,
        manifest_file: DatasetManifestFile,
        created_at: str,
    ) -> DatasetManifest:
        return {
            "version_id": version_id,
            "dataset": dataset_ref,
            "branch": branch,
            "schema_hash": schema_hash,
            "files": [manifest_file],
            "created_at": created_at,
            "storage_profile": self.profile_name,
        }

    def _verify_committed_manifest(self, manifest_key: str, manifest_file: DatasetManifestFile) -> None:
        manifest = self.load_manifest(self._uri_for_key(manifest_key))
        files = manifest.get("files") or []
        if not files or files[0]["uri"] != manifest_file["uri"]:
            raise ValueError("S3 manifest does not reference the committed data object")
        # _download_manifest_file re-verifies byte size and content hash against the
        # manifest, so committing and later reads share one integrity check.
        self._download_manifest_file(manifest_file)

    def _download_manifest_file(self, manifest_file: DatasetManifestFile) -> Path:
        key = self._key_for_uri(manifest_file["uri"])
        target = self.cache_root / "downloads" / key
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._client.download_file(self.config.bucket, key, str(target))
        except Exception as exc:
            if _is_not_found_error(exc):
                raise FileNotFoundError(self._uri_for_key(key)) from exc
            raise self._adapter_error("load_manifest", exc) from exc
        self._verify_local_copy(target, manifest_file)
        return target

    def _verify_local_copy(self, target: Path, manifest_file: DatasetManifestFile) -> None:
        # Integrity is checked on every download, not just at commit time: a committed
        # object can still be truncated or silently corrupted in storage afterwards.
        expected_size = manifest_file.get("byte_size")
        if expected_size is not None and target.stat().st_size != expected_size:
            raise ValueError(f"S3 data object byte size mismatch: {manifest_file['uri']}")
        expected_hash = manifest_file.get("content_hash")
        if expected_hash is not None and _file_hash(target) != expected_hash:
            raise ValueError(f"S3 data object content hash mismatch: {manifest_file['uri']}")

    def _put_json(self, key: str, payload: DatasetManifest) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self._client.put_object(Bucket=self.config.bucket, Key=key, Body=body)

    def _get_object_bytes(self, key: str) -> bytes:
        try:
            return cast(bytes, self._client.get_object(Bucket=self.config.bucket, Key=key)["Body"].read())
        except Exception as exc:
            # Only a genuine "missing object" means storage corruption. Transient
            # errors (timeout, throttling, connection reset) must stay retryable
            # instead of being mislabelled as a non-retryable corrupt version.
            if _is_not_found_error(exc):
                raise FileNotFoundError(self._uri_for_key(key)) from exc
            raise self._adapter_error("load_manifest", exc) from exc

    def _guard_version_not_committed(self, manifest_key: str, version_id: str) -> None:
        if self._object_exists(manifest_key):
            raise AdapterError(
                AdapterFailure(
                    self.profile_name,
                    "commit_staged_file",
                    "conflict",
                    False,
                    f"S3 dataset version already committed: {version_id}",
                    idempotency_key=version_id,
                )
            )

    def _object_exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.config.bucket, Key=key)
        except Exception as exc:
            if _is_not_found_error(exc):
                return False
            raise self._adapter_error("commit_staged_file", exc) from exc
        return True

    def _safe_cleanup_failed_commit(self, *keys: str) -> dict[str, object]:
        # Best-effort cleanup that never masks the original commit failure. Deletes
        # by known key (not by listing) so list-after-write eventual consistency
        # cannot hide a just-written orphan, and aborts any incomplete multipart
        # uploads whose parts never appear in list_objects_v2.
        errors: list[str] = []
        try:
            self._abort_multipart_uploads(keys)
        except Exception as exc:  # noqa: BLE001 - cleanup must not raise
            errors.append(f"abort_multipart: {exc}")
        for key in keys:
            try:
                self._client.delete_object(Bucket=self.config.bucket, Key=key)
            except Exception as exc:  # noqa: BLE001 - cleanup must not raise
                errors.append(f"delete {key}: {exc}")
        if errors:
            return {"orphan_cleanup": "incomplete", "orphan_keys": list(keys), "cleanup_errors": errors}
        return {"orphan_cleanup": "completed"}

    def _abort_multipart_uploads(self, keys: tuple[str, ...]) -> None:
        for key in keys:
            response = self._client.list_multipart_uploads(Bucket=self.config.bucket, Prefix=key)
            for upload in response.get("Uploads") or []:
                if upload.get("Key") == key:
                    self._client.abort_multipart_upload(Bucket=self.config.bucket, Key=key, UploadId=upload["UploadId"])

    def _delete_prefix(self, prefix: str) -> bool:
        removed = False
        for key in self._list_keys(prefix):
            self._client.delete_object(Bucket=self.config.bucket, Key=key)
            removed = True
        return removed

    def _list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        continuation_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.config.bucket, "Prefix": prefix}
            if continuation_token is not None:
                kwargs["ContinuationToken"] = continuation_token
            response = self._client.list_objects_v2(**kwargs)
            keys.extend(str(item["Key"]) for item in (response.get("Contents") or []) if "Key" in item)
            if not response.get("IsTruncated"):
                break
            continuation_token = response.get("NextContinuationToken")
            if not continuation_token:
                break
        return keys

    def _ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self.config.bucket)
        except Exception:
            self._client.create_bucket(Bucket=self.config.bucket)

    def _dataset_prefix(self, tenant_id: str, dataset_id: str) -> str:
        return _join_key(self.config.prefix, tenant_id, "datasets", dataset_id)

    def _version_prefix(self, tenant_id: str, dataset_id: str, branch: str, version_id: str) -> str:
        return _join_key(self._dataset_prefix(tenant_id, dataset_id), f"branch={branch}", f"version={version_id}")

    def _uri_for_key(self, key: str) -> str:
        return f"s3://{self.config.bucket}/{key}"

    def _key_for_uri(self, uri: str) -> str:
        prefix = f"s3://{self.config.bucket}/"
        if not uri.startswith(prefix):
            raise ValueError(f"S3 URI is outside configured bucket: {uri}")
        return uri.removeprefix(prefix)

    def _adapter_error(
        self,
        operation: str,
        exc: Exception,
        *,
        idempotency_key: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> AdapterError:
        kind: AdapterFailureKind = "timeout" if isinstance(exc, TimeoutError) else "unavailable"
        return AdapterError(
            AdapterFailure(
                self.profile_name,
                operation,
                kind,
                True,
                f"S3 dataset storage {operation} failed: {exc}",
                timeout_seconds=30 if kind == "timeout" else None,
                idempotency_key=idempotency_key,
                details=details or {},
            )
        )


def _boto3_s3_client(config: S3DatasetStorageAdapterConfig) -> Any:
    return build_s3_client(
        endpoint_url=config.endpoint_url,
        access_key_id=config.access_key_id,
        secret_access_key=config.secret_access_key,
        region_name=config.region_name,
    )


_is_not_found_error = is_not_found_error


def _matches_partition_filter(
    manifest_file: DatasetManifestFile,
    partition_filter: Mapping[str, object],
) -> bool:
    partition_values = manifest_file.get("partition_values") or {}
    return all(partition_values.get(key) == value for key, value in partition_filter.items())


_join_key = join_key
