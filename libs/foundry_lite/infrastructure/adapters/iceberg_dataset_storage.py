"""Iceberg-backed dataset storage adapter (table catalog + object storage)."""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any

from foundry_lite.application.ports import DatasetManifest, DatasetManifestFile, StoredDatasetCommit
from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureKind,
    AdapterFailureMode,
)

_VERSION_PROP = "foundry.version_id"
_DATASET_PROP = "foundry.dataset_ref"
_SCHEMA_PROP = "foundry.schema_hash"
_URI_SCHEME = "iceberg://"


@dataclass(frozen=True)
class IcebergDatasetStorageAdapterConfig:
    """Wiring for an Iceberg SQL catalog whose data/metadata live in object storage."""

    catalog_uri: str
    warehouse: str
    namespace: str = "foundry_lite"
    cache_root: Path = Path(".foundry-lite/iceberg-cache")
    s3_endpoint_url: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = field(default=None, repr=False)
    s3_region: str = "us-east-1"
    s3_path_style_enabled: bool = True


class IcebergDatasetStorageAdapter:
    """Iceberg-backed dataset storage: each committed version is one table snapshot.

    The data files and table metadata live in object storage (S3/MinIO); the SQL
    catalog tracks the current metadata pointer. A Foundry dataset version pins an
    exact Iceberg ``snapshot_id`` so reads stay isolated from later commits.
    """

    profile_name = "iceberg"

    def __init__(self, config: IcebergDatasetStorageAdapterConfig, *, catalog: Any | None = None) -> None:
        """Init."""
        self.config = config
        self.cache_root = Path(config.cache_root).resolve()
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._catalog = catalog or _build_sql_catalog(config)

    def failure_contract(self) -> AdapterFailureContract:
        """Failure contract."""
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "commit_staged_file",
                    "timeout",
                    True,
                    "Iceberg snapshot commit outcome is unknown; retry with the same version id after a catalog check.",
                    timeout_seconds=30,
                    has_required_idempotency_key=True,
                ),
                AdapterFailureMode(
                    "commit_staged_file",
                    "conflict",
                    False,
                    "Iceberg table already holds this dataset version id; the allocator must hand out a fresh id.",
                ),
                AdapterFailureMode(
                    "load_manifest",
                    "not_found",
                    False,
                    "Iceberg table or pinned snapshot is missing; treat the committed version as storage-corrupt.",
                ),
                AdapterFailureMode(
                    "load_manifest",
                    "unavailable",
                    True,
                    "Iceberg catalog/storage read could not be reached; this is transient, not corruption — retry.",
                ),
                AdapterFailureMode(
                    "delete_committed_version",
                    "unavailable",
                    True,
                    "Iceberg orphan-snapshot cleanup could not reach the catalog; retry before advancing the ratchet.",
                ),
            ),
        )

    def dataset_uri(self, tenant_id: str, dataset_id: str) -> str:
        """Dataset uri."""
        return f"{_URI_SCHEME}{self.config.namespace}/{self._table_name(tenant_id, dataset_id, 'main')}"

    def staging_file(self, *, tenant_id: str, dataset_id: str, transaction_id: str, file_name: str) -> Path:
        """Staging file."""
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
        """Commit staged file."""
        identifier = self._identifier(tenant_id, dataset_id, branch)
        self._guard_version_not_committed(identifier, version_id)
        try:
            return self._commit_snapshot(
                identifier, version_id, dataset_ref, branch, schema_hash, staged_file, created_at
            )
        except AdapterError as exc:
            if exc.failure.kind != "conflict":
                self._safe_rollback_version(identifier, version_id)
            raise
        except Exception as exc:
            cleanup = self._safe_rollback_version(identifier, version_id)
            raise self._adapter_error("commit_staged_file", exc, idempotency_key=version_id, details=cleanup) from exc

    def delete_committed_version(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        branch: str,
        version_id: str,
    ) -> bool:
        """Delete committed version."""
        identifier = self._identifier(tenant_id, dataset_id, branch)
        return self._rollback_version(identifier, version_id)

    def delete_staging_transaction(self, *, tenant_id: str, dataset_id: str, transaction_id: str) -> bool:
        """Delete staging transaction."""
        staging_dir = self.cache_root / "staging" / tenant_id / dataset_id / transaction_id
        if not staging_dir.exists():
            return False
        shutil.rmtree(staging_dir)
        return True

    def load_manifest(self, manifest_uri: str) -> DatasetManifest:
        """Load manifest."""
        identifier, snapshot_id = self._parse_manifest_uri(manifest_uri)
        table = self._load_table(identifier)
        snapshot = table.snapshot_by_id(snapshot_id)
        if snapshot is None:
            raise FileNotFoundError(manifest_uri)
        props = snapshot.summary.additional_properties
        files = self._manifest_files(table, snapshot_id)
        return {
            "version_id": props.get(_VERSION_PROP, ""),
            "dataset": props.get(_DATASET_PROP, ""),
            "branch": identifier.rsplit(".", 1)[-1],
            "schema_hash": props.get(_SCHEMA_PROP, ""),
            "files": files,
            "created_at": "",
            "storage_profile": self.profile_name,
        }

    def first_data_file_path(self, manifest_uri: str) -> Path:
        """First data file path."""
        identifier, snapshot_id = self._parse_manifest_uri(manifest_uri)
        table = self._load_table(identifier)
        if table.snapshot_by_id(snapshot_id) is None:
            raise FileNotFoundError(manifest_uri)
        self._verify_snapshot_files(table, snapshot_id, manifest_uri)
        return self._materialize_snapshot(table, snapshot_id, identifier)

    # -- commit helpers ---------------------------------------------------

    def _commit_snapshot(
        self,
        identifier: str,
        version_id: str,
        dataset_ref: str,
        branch: str,
        schema_hash: str,
        staged_file: Path,
        created_at: str,
    ) -> StoredDatasetCommit:
        """Commit snapshot."""
        pq = import_module("pyarrow.parquet")
        arrow_table = pq.read_table(str(staged_file))
        self._load_or_create_table(identifier, arrow_table.schema)
        # The engine validates schema compatibility upstream; here we mirror an
        # additive (compatible) evolution into the Iceberg table so a later version's
        # new columns do not break the overwrite.
        self._evolve_schema_if_needed(identifier, arrow_table.schema)
        table = self._reload(identifier)
        # Each Foundry version is the complete content for that version, so SNAPSHOT
        # semantics (overwrite) apply and the new current snapshot is this version.
        table.overwrite(
            arrow_table,
            snapshot_properties={
                _VERSION_PROP: version_id,
                _DATASET_PROP: dataset_ref,
                _SCHEMA_PROP: schema_hash,
                "foundry.created_at": created_at,
            },
        )
        snapshot = self._reload(identifier).current_snapshot()
        if snapshot is None:
            raise ValueError("Iceberg overwrite did not produce a snapshot")
        snapshot_id = snapshot.snapshot_id
        manifest_uri = self._manifest_uri(identifier, snapshot_id)
        files = self._manifest_files(self._load_table(identifier), snapshot_id)
        return StoredDatasetCommit(
            manifest_uri=manifest_uri,
            data_file_uri=files[0]["uri"] if files else manifest_uri,
            data_file_path=self._materialize_snapshot(self._load_table(identifier), snapshot_id, identifier),
            byte_size=sum(f["byte_size"] for f in files),
            content_hash=_snapshot_token(snapshot_id, files),
            manifest={
                "version_id": version_id,
                "dataset": dataset_ref,
                "branch": branch,
                "schema_hash": schema_hash,
                "files": files,
                "created_at": created_at,
                "storage_profile": self.profile_name,
            },
        )

    def _guard_version_not_committed(self, identifier: str, version_id: str) -> None:
        """Guard version not committed."""
        if self._find_snapshot_id(identifier, version_id) is not None:
            raise AdapterError(
                AdapterFailure(
                    self.profile_name,
                    "commit_staged_file",
                    "conflict",
                    False,
                    f"Iceberg dataset version already committed: {version_id}",
                    idempotency_key=version_id,
                )
            )

    def _safe_rollback_version(self, identifier: str, version_id: str) -> Mapping[str, object]:
        """Safe rollback version."""
        try:
            removed = self._rollback_version(identifier, version_id)
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask the original failure
            return {"orphan_cleanup": "incomplete", "version_id": version_id, "cleanup_error": str(exc)}
        return {"orphan_cleanup": "completed" if removed else "nothing_to_clean", "version_id": version_id}

    def _rollback_version(self, identifier: str, version_id: str) -> bool:
        """Rollback version."""
        try:
            table = self._load_table(identifier)
        except FileNotFoundError:
            return False
        current = table.current_snapshot()
        if current is None or current.summary.additional_properties.get(_VERSION_PROP) != version_id:
            # Only the just-committed (current) version is rolled back here; older
            # orphans are handled by the dedicated reconciliation path (ratchet phase).
            return False
        target = self._ancestor_before_version(table, current, version_id)
        if target is None:
            self._catalog.drop_table(identifier)
            return True
        table.manage_snapshots().rollback_to_snapshot(target).commit()
        return True

    # -- read helpers -----------------------------------------------------

    def _manifest_files(self, table: Any, snapshot_id: int) -> list[DatasetManifestFile]:
        """Manifest files."""
        files: list[DatasetManifestFile] = []
        for task in table.scan(snapshot_id=snapshot_id).plan_files():
            data = task.file
            files.append(
                {
                    "uri": str(data.file_path),
                    "format": "parquet",
                    "row_count": int(data.record_count),
                    "byte_size": int(data.file_size_in_bytes),
                    "content_hash": _file_token(
                        str(data.file_path), int(data.file_size_in_bytes), int(data.record_count)
                    ),
                }
            )
        return files

    def _verify_snapshot_files(self, table: Any, snapshot_id: int, manifest_uri: str) -> None:
        """Verify snapshot files."""
        io = table.io
        for task in table.scan(snapshot_id=snapshot_id).plan_files():
            data = task.file
            try:
                input_file = io.new_input(data.file_path)
                exists = input_file.exists()
                actual = input_file.__len__() if exists else -1
            except Exception as exc:  # noqa: BLE001 - classified below
                raise self._adapter_error("load_manifest", exc) from exc
            if not exists:
                raise FileNotFoundError(f"{manifest_uri}::{data.file_path}")
            if actual != int(data.file_size_in_bytes):
                raise ValueError(f"Iceberg data file size mismatch: {data.file_path}")

    def _materialize_snapshot(self, table: Any, snapshot_id: int, identifier: str) -> Path:
        """Materialize snapshot."""
        pq = import_module("pyarrow.parquet")
        arrow_table = table.scan(snapshot_id=snapshot_id).to_arrow()
        target = self.cache_root / "downloads" / identifier.replace(".", "/") / f"snapshot-{snapshot_id}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(arrow_table, str(target))
        return target

    # -- catalog/table helpers -------------------------------------------

    def _evolve_schema_if_needed(self, identifier: str, arrow_schema: Any) -> None:
        """Evolve schema if needed."""
        table = self._reload(identifier)
        existing = {field_.name for field_ in table.schema().fields}
        if set(arrow_schema.names) <= existing:
            return
        with table.update_schema() as update:
            update.union_by_name(arrow_schema)

    def _load_or_create_table(self, identifier: str, schema: Any) -> Any:
        """Load or create table."""
        self._ensure_namespace()
        try:
            return self._catalog.load_table(identifier)
        except Exception:
            return self._catalog.create_table(identifier, schema=schema)

    def _load_table(self, identifier: str) -> Any:
        """Load table."""
        try:
            return self._catalog.load_table(identifier)
        except Exception as exc:
            # A missing table/metadata is a corruption-missing case; corrupt metadata
            # (unparseable) is corruption proper; everything else is transient and
            # must stay retryable rather than be mislabelled as corruption.
            if _is_missing_table(exc):
                raise FileNotFoundError(identifier) from exc
            if _is_corrupt_metadata(exc):
                raise ValueError(f"Iceberg table metadata is corrupt: {identifier}: {exc}") from exc
            raise self._adapter_error("load_manifest", exc) from exc

    def _reload(self, identifier: str) -> Any:
        """Reload."""
        return self._catalog.load_table(identifier)

    def _ensure_namespace(self) -> None:
        """Ensure namespace."""
        try:
            self._catalog.create_namespace(self.config.namespace)
        except Exception as exc:  # noqa: BLE001 - only the already-exists case is expected
            # Swallow "namespace already exists"; re-raise anything else (e.g. a
            # catalog outage) so it is not silently hidden during commit.
            if "AlreadyExists" not in type(exc).__name__ and "exists" not in str(exc).lower():
                raise

    def _find_snapshot_id(self, identifier: str, version_id: str) -> int | None:
        """Find snapshot id."""
        try:
            table = self._catalog.load_table(identifier)
        except Exception:
            return None
        for snapshot in table.snapshots():
            if snapshot.summary.additional_properties.get(_VERSION_PROP) == version_id:
                return int(snapshot.snapshot_id)
        return None

    def _ancestor_before_version(self, table: Any, current: Any, version_id: str) -> int | None:
        """Ancestor before version."""
        by_id = {s.snapshot_id: s for s in table.snapshots()}
        cursor = current
        while cursor is not None and cursor.summary.additional_properties.get(_VERSION_PROP) == version_id:
            parent_id = cursor.parent_snapshot_id
            cursor = by_id.get(parent_id) if parent_id is not None else None
        return int(cursor.snapshot_id) if cursor is not None else None

    def _identifier(self, tenant_id: str, dataset_id: str, branch: str) -> str:
        """Identifier."""
        return f"{self.config.namespace}.{self._table_name(tenant_id, dataset_id, branch)}"

    def _table_name(self, tenant_id: str, dataset_id: str, branch: str) -> str:
        """Table name."""
        return _sanitize(f"{tenant_id}__{dataset_id}__{branch}")

    def _manifest_uri(self, identifier: str, snapshot_id: int) -> str:
        """Manifest uri."""
        namespace, table = identifier.rsplit(".", 1)
        return f"{_URI_SCHEME}{namespace}/{table}@{snapshot_id}"

    def _parse_manifest_uri(self, manifest_uri: str) -> tuple[str, int]:
        """Parse manifest uri."""
        if not manifest_uri.startswith(_URI_SCHEME):
            raise ValueError(f"not an Iceberg manifest uri: {manifest_uri}")
        body, _, snapshot = manifest_uri.removeprefix(_URI_SCHEME).partition("@")
        namespace, _, table = body.rpartition("/")
        if not snapshot.isdigit():
            raise ValueError(f"Iceberg manifest uri missing snapshot id: {manifest_uri}")
        return f"{namespace}.{table}", int(snapshot)

    def _adapter_error(
        self,
        operation: str,
        exc: Exception,
        *,
        idempotency_key: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> AdapterError:
        """Adapter error."""
        kind: AdapterFailureKind = "timeout" if isinstance(exc, TimeoutError) else "unavailable"
        return AdapterError(
            AdapterFailure(
                self.profile_name,
                operation,
                kind,
                True,
                f"Iceberg dataset storage {operation} failed: {exc}",
                timeout_seconds=30 if kind == "timeout" else None,
                idempotency_key=idempotency_key,
                details=details or {},
            )
        )


def _build_sql_catalog(config: IcebergDatasetStorageAdapterConfig) -> Any:
    """Build sql catalog."""
    sql = import_module("pyiceberg.catalog.sql")
    properties: dict[str, str] = {"uri": config.catalog_uri, "warehouse": config.warehouse}
    if config.warehouse.startswith("s3://"):
        properties["s3.endpoint"] = config.s3_endpoint_url or ""
        properties["s3.access-key-id"] = config.s3_access_key_id or ""
        properties["s3.secret-access-key"] = config.s3_secret_access_key or ""
        properties["s3.region"] = config.s3_region
        properties["s3.path-style-access"] = "true" if config.s3_path_style_enabled else "false"
    return sql.SqlCatalog("foundry_lite", **{k: v for k, v in properties.items() if v != ""})


def _is_missing_table(exc: Exception) -> bool:
    """Is missing table."""
    name = type(exc).__name__
    return "NoSuchTable" in name or "NoSuchNamespace" in name or isinstance(exc, FileNotFoundError)


def _is_corrupt_metadata(exc: Exception) -> bool:
    # pyiceberg raises ValidationError when a table metadata document fails to parse.
    """Is corrupt metadata."""
    return type(exc).__name__ == "ValidationError"


def _sanitize(value: str) -> str:
    """Sanitize."""
    return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in value)


def _file_token(path: str, size: int, records: int) -> str:
    """File token."""
    return hashlib.sha256(f"{path}|{size}|{records}".encode()).hexdigest()


def _snapshot_token(snapshot_id: int, files: list[DatasetManifestFile]) -> str:
    """Snapshot token."""
    parts = "|".join(sorted(f"{f['uri']}:{f['byte_size']}:{f['row_count']}" for f in files))
    return hashlib.sha256(f"{snapshot_id}|{parts}".encode()).hexdigest()
