"""Iceberg-backed dataset storage adapter (table catalog + object storage)."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any, cast

from foundry_lite.application.ports import (
    DatasetManifest,
    DatasetManifestFile,
    DatasetVersionRow,
    StoredDatasetCommit,
)
from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureKind,
    AdapterFailureMode,
)
from foundry_lite.application.ports.iceberg_maintenance import (
    IcebergMaintenancePlan,
    IcebergMaintenancePolicy,
    IcebergMaintenanceSnapshot,
)

_VERSION_PROP = "foundry.version_id"
_DATASET_PROP = "foundry.dataset_ref"
_BRANCH_PROP = "foundry.branch"
_SCHEMA_PROP = "foundry.schema_hash"
_CREATED_AT_PROP = "foundry.created_at"
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

    def plan_iceberg_maintenance(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        dataset_ref: str,
        branch: str,
        committed_versions: list[DatasetVersionRow],
        policy: IcebergMaintenancePolicy,
    ) -> IcebergMaintenancePlan:
        """Build a dry-run table maintenance plan without mutating Iceberg state."""
        identifier = self._identifier(tenant_id, dataset_id, branch)
        try:
            table = self._load_table(identifier)
        except FileNotFoundError:
            return _empty_maintenance_plan(self.profile_name, dataset_ref, dataset_id, branch, identifier, policy)
        snapshots = self._maintenance_snapshots(table, identifier, committed_versions, policy)
        return _maintenance_plan(
            self.profile_name,
            dataset_ref,
            dataset_id,
            branch,
            identifier,
            policy,
            snapshots,
            table.current_snapshot(),
        )

    def load_manifest(self, manifest_uri: str) -> DatasetManifest:
        """Load manifest."""
        identifier, snapshot_id = self._parse_manifest_uri(manifest_uri)
        table = self._load_table(identifier)
        snapshot = table.snapshot_by_id(snapshot_id)
        if snapshot is None:
            raise FileNotFoundError(manifest_uri)
        props = snapshot.summary.additional_properties
        sidecar = self._load_sidecar_manifest(table, snapshot_id)
        files = self._manifest_files(table, snapshot_id, expected_files=_expected_files(sidecar))
        return {
            "version_id": props.get(_VERSION_PROP, ""),
            "dataset": props.get(_DATASET_PROP, ""),
            "branch": props.get(_BRANCH_PROP, sidecar.get("branch", "")),
            "schema_hash": props.get(_SCHEMA_PROP, ""),
            "files": files,
            "created_at": props.get(_CREATED_AT_PROP, sidecar.get("created_at", "")),
            "storage_profile": self.profile_name,
        }

    def first_data_file_path(self, manifest_uri: str) -> Path:
        """First data file path."""
        return self.data_file_paths(manifest_uri)[0]

    def data_file_paths(
        self,
        manifest_uri: str,
        *,
        partition_filter: Mapping[str, object] | None = None,
    ) -> list[Path]:
        """Readable data paths for the snapshot represented by a Foundry manifest."""
        manifest = self.load_manifest(manifest_uri)
        files = manifest.get("files") or []
        if partition_filter is not None and not any(
            _matches_partition_filter(file, partition_filter) for file in files
        ):
            return []
        identifier, snapshot_id = self._parse_manifest_uri(manifest_uri)
        table = self._load_table(identifier)
        if table.snapshot_by_id(snapshot_id) is None:
            raise FileNotFoundError(manifest_uri)
        sidecar = self._load_sidecar_manifest(table, snapshot_id)
        expected_files = _expected_files(sidecar)
        self._verify_snapshot_files(table, snapshot_id, manifest_uri, expected_files=expected_files)
        _, has_delete_files = self._snapshot_data_file_uris(table, snapshot_id)
        # The common case is a single coalesced data file with no row-deletes: stream that
        # file directly instead of scanning the whole snapshot into Arrow and rewriting it.
        # Multi-file snapshots and delete files still materialize a single merged file so the
        # single-file read contract (committed_version_file_path -> paths[0]) is preserved.
        if partition_filter is None and len(expected_files) == 1 and not has_delete_files:
            return [self._download_snapshot_file(table, next(iter(expected_files.values())))]
        return [self._materialize_snapshot(table, snapshot_id, identifier)]

    def preview_file_paths(
        self,
        manifest_uri: str,
        *,
        partition_filter: Mapping[str, object] | None = None,
    ) -> list[Path]:
        """Readable data paths for bounded preview without full snapshot materialization."""
        identifier, snapshot_id = self._parse_manifest_uri(manifest_uri)
        table = self._load_table(identifier)
        if table.snapshot_by_id(snapshot_id) is None:
            raise FileNotFoundError(manifest_uri)
        expected_files = _expected_files(self._load_sidecar_manifest(table, snapshot_id))
        actual_files, has_delete_files = self._snapshot_data_file_uris(table, snapshot_id)
        if set(expected_files) != actual_files:
            raise ValueError("Iceberg snapshot file list differs from the committed Foundry manifest")
        if has_delete_files:
            return [self._materialize_snapshot(table, snapshot_id, identifier)]
        files = _matching_expected_files(expected_files, partition_filter)
        return [self._download_snapshot_file(table, file) for file in files]

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
        arrow_table = _iceberg_compatible_arrow_table(pq.read_table(str(staged_file)))
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
                _BRANCH_PROP: branch,
                _SCHEMA_PROP: schema_hash,
                _CREATED_AT_PROP: created_at,
            },
        )
        snapshot = self._reload(identifier).current_snapshot()
        if snapshot is None:
            raise ValueError("Iceberg overwrite did not produce a snapshot")
        snapshot_id = snapshot.snapshot_id
        manifest_uri = self._manifest_uri(identifier, snapshot_id)
        files = self._manifest_files(self._load_table(identifier), snapshot_id)
        content_hash = _snapshot_token(files)
        self._write_sidecar_manifest(
            self._load_table(identifier),
            snapshot_id,
            {
                "version_id": version_id,
                "dataset": dataset_ref,
                "branch": branch,
                "schema_hash": schema_hash,
                "files": files,
                "created_at": created_at,
                "storage_profile": self.profile_name,
            },
            content_hash,
        )
        return StoredDatasetCommit(
            manifest_uri=manifest_uri,
            data_file_uri=files[0]["uri"] if files else manifest_uri,
            data_file_path=self._materialize_snapshot(self._load_table(identifier), snapshot_id, identifier),
            byte_size=sum(f["byte_size"] for f in files),
            content_hash=content_hash,
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

    def _manifest_files(
        self,
        table: Any,
        snapshot_id: int,
        *,
        expected_files: Mapping[str, DatasetManifestFile] | None = None,
    ) -> list[DatasetManifestFile]:
        """Manifest files."""
        files: list[DatasetManifestFile] = []
        actual_uris: set[str] = set()
        for task in table.scan(snapshot_id=snapshot_id).plan_files():
            data = task.file
            uri = str(data.file_path)
            expected = expected_files.get(uri) if expected_files is not None else None
            content_hash = self._hash_iceberg_file(table, uri)
            if expected is not None:
                self._verify_manifest_file(uri, data, content_hash, expected)
            actual_uris.add(uri)
            entry: DatasetManifestFile = {
                "uri": uri,
                "format": "parquet",
                "row_count": int(data.record_count),
                "byte_size": int(data.file_size_in_bytes),
                "content_hash": content_hash,
            }
            if expected is not None and "partition_values" in expected:
                entry["partition_values"] = expected["partition_values"]
            files.append(entry)
        if expected_files is not None and set(expected_files) != actual_uris:
            raise ValueError("Iceberg snapshot file list differs from the committed Foundry manifest")
        return files

    def _verify_snapshot_files(
        self,
        table: Any,
        snapshot_id: int,
        manifest_uri: str,
        *,
        expected_files: Mapping[str, DatasetManifestFile],
    ) -> None:
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
        self._manifest_files(table, snapshot_id, expected_files=expected_files)

    def _verify_manifest_file(
        self,
        uri: str,
        data: Any,
        content_hash: str,
        expected: DatasetManifestFile,
    ) -> None:
        """Verify manifest file."""
        if int(data.file_size_in_bytes) != expected["byte_size"]:
            raise ValueError(f"Iceberg data file byte size differs from committed manifest: {uri}")
        if int(data.record_count) != expected["row_count"]:
            raise ValueError(f"Iceberg data file row count differs from committed manifest: {uri}")
        if content_hash != expected["content_hash"]:
            raise ValueError(f"Iceberg data file content hash differs from committed manifest: {uri}")

    def _maintenance_snapshots(
        self,
        table: Any,
        identifier: str,
        committed_versions: list[DatasetVersionRow],
        policy: IcebergMaintenancePolicy,
    ) -> list[IcebergMaintenanceSnapshot]:
        current = table.current_snapshot()
        committed = _committed_snapshot_versions(committed_versions, self._parse_manifest_uri)
        retained = _retained_snapshot_ids(table.snapshots(), current, committed, policy)
        snapshots: list[IcebergMaintenanceSnapshot] = []
        for snapshot in table.snapshots():
            snapshots.append(
                _maintenance_snapshot(
                    snapshot,
                    self._manifest_uri(identifier, int(snapshot.snapshot_id)),
                    self._manifest_files(table, int(snapshot.snapshot_id)),
                    current_snapshot_id=int(current.snapshot_id) if current is not None else None,
                    committed_version_id=committed.get(int(snapshot.snapshot_id)),
                    retained_snapshot_ids=retained,
                    policy=policy,
                )
            )
        return snapshots

    def _hash_iceberg_file(self, table: Any, uri: str) -> str:
        """Hash iceberg file."""
        digest = hashlib.sha256()
        try:
            with table.io.new_input(uri).open() as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        except Exception as exc:  # noqa: BLE001 - classified by the adapter contract
            raise self._adapter_error("load_manifest", exc) from exc
        return digest.hexdigest()

    def _write_sidecar_manifest(
        self,
        table: Any,
        snapshot_id: int,
        manifest: DatasetManifest,
        content_hash: str,
    ) -> None:
        """Write sidecar manifest."""
        payload = dict(manifest)
        payload["content_hash"] = content_hash
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        output = table.io.new_output(self._sidecar_manifest_location(table, snapshot_id))
        with output.create(overwrite=True) as stream:
            stream.write(body)

    def _load_sidecar_manifest(self, table: Any, snapshot_id: int) -> Mapping[str, object]:
        """Load sidecar manifest."""
        location = self._sidecar_manifest_location(table, snapshot_id)
        sidecar = table.io.new_input(location)
        try:
            if not sidecar.exists():
                raise FileNotFoundError(location)
            with sidecar.open() as stream:
                payload = json.loads(stream.read().decode("utf-8"))
        except FileNotFoundError:
            raise
        except Exception as exc:  # noqa: BLE001 - malformed sidecar is committed-storage corruption
            raise ValueError(f"Iceberg Foundry sidecar manifest is corrupt: {location}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Iceberg Foundry sidecar manifest is not an object: {location}")
        return payload

    def _sidecar_manifest_location(self, table: Any, snapshot_id: int) -> str:
        """Sidecar manifest location."""
        return f"{table.location().rstrip('/')}/foundry-manifests/snapshot-{snapshot_id}.json"

    def _materialize_snapshot(self, table: Any, snapshot_id: int, identifier: str) -> Path:
        """Materialize snapshot."""
        pq = import_module("pyarrow.parquet")
        arrow_table = table.scan(snapshot_id=snapshot_id).to_arrow()
        target = self.cache_root / "downloads" / identifier.replace(".", "/") / f"snapshot-{snapshot_id}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(arrow_table, str(target))
        return target

    def _snapshot_data_file_uris(self, table: Any, snapshot_id: int) -> tuple[set[str], bool]:
        """Return snapshot data-file URIs and whether row deletes affect the scan."""
        uris: set[str] = set()
        has_delete_files = False
        for task in table.scan(snapshot_id=snapshot_id).plan_files():
            uris.add(str(task.file.file_path))
            delete_files = getattr(task, "delete_files", ())
            if callable(delete_files):
                delete_files = delete_files()
            has_delete_files = has_delete_files or bool(delete_files)
        return uris, has_delete_files

    def _download_snapshot_file(self, table: Any, manifest_file: DatasetManifestFile) -> Path:
        """Download one Iceberg data file and verify it against the Foundry manifest."""
        uri = manifest_file["uri"]
        target = self.cache_root / "downloads" / "files" / f"{hashlib.sha256(uri.encode('utf-8')).hexdigest()}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            source = table.io.new_input(uri)
            if not source.exists():
                raise FileNotFoundError(uri)
            with source.open() as source_stream, target.open("wb") as target_stream:
                shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
        except FileNotFoundError:
            raise
        except Exception as exc:  # noqa: BLE001 - classified by the adapter contract
            raise self._adapter_error("load_manifest", exc) from exc
        _verify_downloaded_snapshot_file(target, manifest_file)
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
        except Exception as exc:
            if _is_missing_table(exc):
                return None
            if _is_corrupt_metadata(exc):
                raise ValueError(f"Iceberg table metadata is corrupt: {identifier}: {exc}") from exc
            raise self._adapter_error("commit_staged_file", exc, idempotency_key=version_id) from exc
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


def _expected_files(sidecar: Mapping[str, object]) -> Mapping[str, DatasetManifestFile]:
    """Expected files."""
    files = sidecar.get("files")
    if not isinstance(files, list):
        raise ValueError("Iceberg Foundry sidecar manifest does not contain a file list")
    expected: dict[str, DatasetManifestFile] = {}
    for raw in files:
        if not isinstance(raw, dict):
            raise ValueError("Iceberg Foundry sidecar manifest contains a malformed file entry")
        raw_file = cast(Mapping[str, object], raw)
        entry: DatasetManifestFile = {
            "uri": str(raw_file["uri"]),
            "format": str(raw_file["format"]),
            "row_count": int(str(raw_file["row_count"])),
            "byte_size": int(str(raw_file["byte_size"])),
            "content_hash": str(raw_file["content_hash"]),
        }
        partition_values = raw_file.get("partition_values")
        if isinstance(partition_values, dict):
            entry["partition_values"] = dict(partition_values)
        expected[entry["uri"]] = entry
    return expected


def _matching_expected_files(
    expected_files: Mapping[str, DatasetManifestFile],
    partition_filter: Mapping[str, object] | None,
) -> list[DatasetManifestFile]:
    files = list(expected_files.values())
    if partition_filter is None:
        return files
    return [file for file in files if _matches_partition_filter(file, partition_filter)]


def _verify_downloaded_snapshot_file(target: Path, manifest_file: DatasetManifestFile) -> None:
    if target.stat().st_size != manifest_file["byte_size"]:
        raise ValueError(f"Iceberg data file byte size mismatch: {manifest_file['uri']}")
    if _path_sha256(target) != manifest_file["content_hash"]:
        raise ValueError(f"Iceberg data file content hash mismatch: {manifest_file['uri']}")


def _path_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _matches_partition_filter(
    manifest_file: DatasetManifestFile,
    partition_filter: Mapping[str, object],
) -> bool:
    partition_values = manifest_file.get("partition_values") or {}
    return all(partition_values.get(key) == value for key, value in partition_filter.items())


def _empty_maintenance_plan(
    profile_name: str,
    dataset_ref: str,
    dataset_id: str,
    branch: str,
    identifier: str,
    policy: IcebergMaintenancePolicy,
) -> IcebergMaintenancePlan:
    return {
        "dataset_ref": dataset_ref,
        "dataset_id": dataset_id,
        "branch": branch,
        "storage_profile": profile_name,
        "table_identifier": identifier,
        "current_snapshot_id": None,
        "policy": policy,
        "snapshots": [],
        "compaction_candidates": [],
        "orphan_snapshots": [],
        "protected_snapshot_ids": [],
        "retained_snapshot_ids": [],
        "deletable_snapshot_ids": [],
        "status": "no_table",
    }


def _maintenance_plan(
    profile_name: str,
    dataset_ref: str,
    dataset_id: str,
    branch: str,
    identifier: str,
    policy: IcebergMaintenancePolicy,
    snapshots: list[IcebergMaintenanceSnapshot],
    current: Any | None,
) -> IcebergMaintenancePlan:
    candidates = _snapshots_with_reasons(snapshots)
    orphans = _orphan_snapshots(snapshots)
    return {
        "dataset_ref": dataset_ref,
        "dataset_id": dataset_id,
        "branch": branch,
        "storage_profile": profile_name,
        "table_identifier": identifier,
        "current_snapshot_id": _current_snapshot_id(current),
        "policy": policy,
        "snapshots": snapshots,
        "compaction_candidates": candidates,
        "orphan_snapshots": orphans,
        "protected_snapshot_ids": _protected_snapshot_ids(snapshots),
        "retained_snapshot_ids": _plan_retained_snapshot_ids(snapshots),
        "deletable_snapshot_ids": _deletable_snapshot_ids(orphans),
        "status": _maintenance_plan_status(candidates, orphans),
    }


def _snapshots_with_reasons(snapshots: list[IcebergMaintenanceSnapshot]) -> list[IcebergMaintenanceSnapshot]:
    return [snapshot for snapshot in snapshots if snapshot["reason_codes"]]


def _orphan_snapshots(snapshots: list[IcebergMaintenanceSnapshot]) -> list[IcebergMaintenanceSnapshot]:
    return [snapshot for snapshot in snapshots if "orphan_snapshot" in snapshot["reason_codes"]]


def _protected_snapshot_ids(snapshots: list[IcebergMaintenanceSnapshot]) -> list[int]:
    return sorted(snapshot["snapshot_id"] for snapshot in snapshots if snapshot["is_protected"])


def _plan_retained_snapshot_ids(snapshots: list[IcebergMaintenanceSnapshot]) -> list[int]:
    retained = (
        snapshot["snapshot_id"] for snapshot in snapshots if "retention_min_snapshot" in snapshot["protected_by"]
    )
    return sorted(set(retained))


def _deletable_snapshot_ids(orphans: list[IcebergMaintenanceSnapshot]) -> list[int]:
    return sorted(snapshot["snapshot_id"] for snapshot in orphans if not snapshot["is_protected"])


def _current_snapshot_id(current: Any | None) -> int | None:
    if current is None:
        return None
    return int(current.snapshot_id)


def _maintenance_plan_status(
    candidates: list[IcebergMaintenanceSnapshot],
    orphans: list[IcebergMaintenanceSnapshot],
) -> str:
    if candidates or orphans:
        return "maintenance_needed"
    return "healthy"


def _maintenance_snapshot(
    snapshot: Any,
    manifest_uri: str,
    files: list[DatasetManifestFile],
    *,
    current_snapshot_id: int | None,
    committed_version_id: str | None,
    retained_snapshot_ids: set[int],
    policy: IcebergMaintenancePolicy,
) -> IcebergMaintenanceSnapshot:
    snapshot_id = int(snapshot.snapshot_id)
    file_count = len(files)
    total_bytes = sum(file["byte_size"] for file in files)
    row_count = sum(file["row_count"] for file in files)
    average = int(total_bytes / file_count) if file_count else 0
    protected_by = _protected_reasons(snapshot_id, current_snapshot_id, committed_version_id, retained_snapshot_ids)
    return {
        "snapshot_id": snapshot_id,
        "version_id": _snapshot_version_id(snapshot, committed_version_id),
        "manifest_uri": manifest_uri,
        "file_count": file_count,
        "row_count": row_count,
        "total_bytes": total_bytes,
        "average_file_size_bytes": average,
        "read_amplification": float(file_count),
        "is_current": snapshot_id == current_snapshot_id,
        "is_protected": bool(protected_by),
        "protected_by": protected_by,
        "reason_codes": _maintenance_reason_codes(file_count, average, committed_version_id, policy),
    }


def _protected_reasons(
    snapshot_id: int,
    current_snapshot_id: int | None,
    committed_version_id: str | None,
    retained_snapshot_ids: set[int],
) -> list[str]:
    reasons: list[str] = []
    if snapshot_id == current_snapshot_id:
        reasons.append("current_snapshot")
    if committed_version_id is not None:
        reasons.append(f"committed_db_version:{committed_version_id}")
    if snapshot_id in retained_snapshot_ids:
        reasons.append("retention_min_snapshot")
    return reasons


def _maintenance_reason_codes(
    file_count: int,
    average_file_size_bytes: int,
    committed_version_id: str | None,
    policy: IcebergMaintenancePolicy,
) -> list[str]:
    reasons: list[str] = []
    if committed_version_id is None:
        reasons.append("orphan_snapshot")
    if file_count > policy["file_count_threshold"]:
        reasons.append("too_many_files")
    if file_count and average_file_size_bytes < policy["small_file_threshold_bytes"]:
        reasons.append("small_files")
    if float(file_count) > policy["read_amplification_threshold"]:
        reasons.append("read_amplification")
    return reasons


def _snapshot_version_id(snapshot: Any, committed_version_id: str | None) -> str | None:
    value = snapshot.summary.additional_properties.get(_VERSION_PROP)
    return value if isinstance(value, str) and value else committed_version_id


def _committed_snapshot_versions(
    committed_versions: list[DatasetVersionRow],
    parse_manifest_uri: object,
) -> dict[int, str]:
    parser = cast(Callable[[str], tuple[str, int]], parse_manifest_uri)
    committed: dict[int, str] = {}
    for version in committed_versions:
        try:
            _, snapshot_id = parser(version["manifest_uri"])
        except ValueError:
            continue
        committed[snapshot_id] = version["id"]
    return committed


def _retained_snapshot_ids(
    snapshots: Sequence[Any],
    current: Any | None,
    committed: Mapping[int, str],
    policy: IcebergMaintenancePolicy,
) -> set[int]:
    retained = set(committed)
    if current is not None:
        retained.add(int(current.snapshot_id))
    minimum = policy["retention_min_snapshots"]
    retained.update(int(snapshot.snapshot_id) for snapshot in snapshots[-minimum:])
    return retained


def _iceberg_compatible_arrow_table(arrow_table: Any) -> Any:
    """Normalize Arrow types that Iceberg v2 cannot commit as-is."""
    pa = import_module("pyarrow")
    schema = arrow_table.schema
    fields = [_iceberg_compatible_field(pa, field) for field in schema]
    converted_schema = pa.schema(fields, metadata=schema.metadata)
    if converted_schema.equals(schema, check_metadata=True):
        return arrow_table
    return arrow_table.cast(converted_schema, safe=False)


def _iceberg_compatible_field(pa: Any, field: Any) -> Any:
    """Return the field with an Iceberg-compatible type, preserving field metadata."""
    converted_type = _iceberg_compatible_type(pa, field.type)
    if converted_type == field.type:
        return field
    return field.with_type(converted_type)


def _iceberg_compatible_type(pa: Any, data_type: Any) -> Any:
    """Return an Iceberg-compatible Arrow type."""
    if pa.types.is_timestamp(data_type) and data_type.unit == "ns":
        return pa.timestamp("us", tz=data_type.tz)
    if pa.types.is_struct(data_type):
        return pa.struct([_iceberg_compatible_field(pa, child) for child in data_type])
    if pa.types.is_fixed_size_list(data_type):
        return pa.list_(_iceberg_compatible_field(pa, data_type.value_field), list_size=data_type.list_size)
    if pa.types.is_list(data_type):
        return pa.list_(_iceberg_compatible_field(pa, data_type.value_field))
    if pa.types.is_large_list(data_type):
        return pa.large_list(_iceberg_compatible_field(pa, data_type.value_field))
    if pa.types.is_map(data_type):
        key_field = _iceberg_compatible_field(pa, data_type.key_field)
        item_field = _iceberg_compatible_field(pa, data_type.item_field)
        return pa.map_(key_field.type, item_field.type, keys_sorted=data_type.keys_sorted)
    return data_type


def _snapshot_token(files: list[DatasetManifestFile]) -> str:
    """Snapshot token."""
    if len(files) == 1:
        return files[0]["content_hash"]
    parts = "|".join(sorted(f"{f['content_hash']}:{f['byte_size']}:{f['row_count']}" for f in files))
    return hashlib.sha256(parts.encode()).hexdigest()
