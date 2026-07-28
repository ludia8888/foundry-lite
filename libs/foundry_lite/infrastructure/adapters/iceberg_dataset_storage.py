"""Iceberg-backed dataset storage adapter (table catalog + object storage)."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any

from foundry_lite.application.ports import (
    DatasetManifest,
    DatasetManifestFile,
    DatasetStagedFile,
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
    IcebergMaintenanceRun,
    IcebergMaintenanceSnapshot,
)
from foundry_lite.infrastructure.adapters.dataset_manifest_metadata import parquet_manifest_file_metadata
from foundry_lite.infrastructure.adapters.iceberg_arrow import (
    _arrow_schema_hash,
    _arrow_table_row_hash,
    _iceberg_compatible_arrow_table,
)
from foundry_lite.infrastructure.adapters.iceberg_maintenance import (
    _committed_snapshot_versions,
    _current_compaction_candidate,
    _empty_maintenance_plan,
    _empty_maintenance_run,
    _empty_rewrite_result,
    _maintenance_plan,
    _maintenance_run,
    _maintenance_snapshot,
    _maintenance_snapshot_properties,
    _MaintenanceRewriteResult,
    _retained_snapshot_ids,
    _rewrite_result,
    _utc_now_iso,
)
from foundry_lite.infrastructure.adapters.iceberg_manifests import (
    _BRANCH_PROP,
    _CREATED_AT_PROP,
    _DATASET_PROP,
    _SCHEMA_PROP,
    _VERSION_PROP,
    _copy_expected_manifest_metadata,
    _dataset_manifest,
    _expected_files,
    _files_by_uri,
    _matches_manifest_filter,
    _matching_expected_files,
    _path_sha256,
    _snapshot_properties,
    _snapshot_token,
    _staged_files_row_count,
    _uploaded_row_count,
    _verify_downloaded_snapshot_file,
)

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
                    "commit_staged_files",
                    "timeout",
                    True,
                    "Iceberg multi-part snapshot commit outcome is unknown; retry with the same version id after "
                    "a catalog check.",
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
                    "commit_staged_files",
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
                AdapterFailureMode(
                    "run_iceberg_maintenance",
                    "unavailable",
                    True,
                    "Iceberg maintenance execution could not reach the catalog/storage; retry after checking "
                    "the maintenance audit evidence.",
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
        sort_order: Sequence[str] | None = None,
    ) -> StoredDatasetCommit:
        """Commit staged file."""
        return self._commit_staged_files(
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
            operation="commit_staged_file",
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
        """Commit staged files as one Iceberg snapshot."""
        return self._commit_staged_files(
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
            operation="commit_staged_files",
        )

    def _commit_staged_files(
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
        sort_order: Sequence[str] | None,
        operation: str,
    ) -> StoredDatasetCommit:
        if not staged_files:
            raise ValueError("dataset commit requires at least one staged file")
        if _staged_files_row_count(staged_files, row_count) != row_count:
            raise ValueError("dataset staged file row counts do not match validation row count")
        identifier = self._identifier(tenant_id, dataset_id, branch)
        self._guard_version_not_committed(identifier, version_id, operation)
        try:
            if len(staged_files) == 1:
                staged_file = self._merged_staged_file(version_id, staged_files)
                return self._commit_snapshot(
                    identifier, version_id, dataset_ref, branch, schema_hash, staged_file, created_at, sort_order
                )
            return self._commit_snapshot_files(
                identifier, version_id, dataset_ref, branch, schema_hash, staged_files, created_at, sort_order
            )
        except AdapterError as exc:
            if exc.failure.kind != "conflict":
                self._safe_rollback_version(identifier, version_id)
            raise
        except Exception as exc:
            cleanup = self._safe_rollback_version(identifier, version_id)
            raise self._adapter_error(operation, exc, idempotency_key=version_id, details=cleanup) from exc

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

    def run_iceberg_maintenance(
        self,
        *,
        tenant_id: str,
        dataset_id: str,
        dataset_ref: str,
        branch: str,
        committed_versions: list[DatasetVersionRow],
        policy: IcebergMaintenancePolicy,
    ) -> IcebergMaintenanceRun:
        """Execute compaction plus safe orphan snapshot expiration."""
        identifier = self._identifier(tenant_id, dataset_id, branch)
        try:
            table = self._load_table(identifier)
        except FileNotFoundError:
            return _empty_maintenance_run(self.profile_name, dataset_ref, dataset_id, branch, identifier, policy)
        before_plan = self.plan_iceberg_maintenance(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            dataset_ref=dataset_ref,
            branch=branch,
            committed_versions=committed_versions,
            policy=policy,
        )
        rewrite = self._rewrite_current_snapshot_if_needed(table, identifier, dataset_ref, branch, before_plan)
        expiration_plan = self.plan_iceberg_maintenance(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            dataset_ref=dataset_ref,
            branch=branch,
            committed_versions=committed_versions,
            policy=policy,
        )
        expired, skipped, cleaned = self._expire_deletable_snapshots(identifier, expiration_plan)
        after_plan = self.plan_iceberg_maintenance(
            tenant_id=tenant_id,
            dataset_id=dataset_id,
            dataset_ref=dataset_ref,
            branch=branch,
            committed_versions=committed_versions,
            policy=policy,
        )
        return _maintenance_run(
            self.profile_name,
            before_plan,
            after_plan,
            rewrite,
            expired_snapshot_ids=expired,
            skipped_expiration_snapshot_ids=skipped,
            orphan_cleanup_file_uris=cleaned,
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
        if partition_filter is not None and not any(_matches_manifest_filter(file, partition_filter) for file in files):
            return []
        identifier, snapshot_id = self._parse_manifest_uri(manifest_uri)
        table = self._load_table(identifier)
        if table.snapshot_by_id(snapshot_id) is None:
            raise FileNotFoundError(manifest_uri)
        sidecar = self._load_sidecar_manifest(table, snapshot_id)
        expected_files = _expected_files(sidecar)
        self._verify_snapshot_files(table, snapshot_id, manifest_uri, expected_files=expected_files)
        _, has_delete_files = self._snapshot_data_file_uris(table, snapshot_id)
        matched_files = _matching_expected_files(expected_files, partition_filter)
        if not matched_files:
            return []
        if has_delete_files:
            return [self._materialize_snapshot(table, snapshot_id, identifier)]
        return [self._download_snapshot_file(table, file) for file in matched_files]

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
        sort_order: Sequence[str] | None,
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
        # semantics apply and the new current snapshot is this version. A newly
        # created table has nothing to replace; append avoids PyIceberg's no-op
        # delete warning while producing the same initial snapshot.
        snapshot_properties = _snapshot_properties(version_id, dataset_ref, branch, schema_hash, created_at)
        if table.current_snapshot() is None:
            table.append(arrow_table, snapshot_properties=snapshot_properties)
        else:
            table.overwrite(arrow_table, snapshot_properties=snapshot_properties)
        snapshot = self._reload(identifier).current_snapshot()
        if snapshot is None:
            raise ValueError("Iceberg overwrite did not produce a snapshot")
        snapshot_id = snapshot.snapshot_id
        manifest_uri = self._manifest_uri(identifier, snapshot_id)
        files = self._manifest_files(self._load_table(identifier), snapshot_id, sort_order=sort_order)
        content_hash = _snapshot_token(files)
        manifest = _dataset_manifest(version_id, dataset_ref, branch, schema_hash, files, created_at, self.profile_name)
        self._write_sidecar_manifest(
            self._load_table(identifier),
            snapshot_id,
            manifest,
            content_hash,
        )
        return StoredDatasetCommit(
            manifest_uri=manifest_uri,
            data_file_uri=files[0]["uri"] if files else manifest_uri,
            data_file_path=self._materialize_snapshot(self._load_table(identifier), snapshot_id, identifier),
            byte_size=sum(f["byte_size"] for f in files),
            content_hash=content_hash,
            manifest=manifest,
        )

    def _commit_snapshot_files(
        self,
        identifier: str,
        version_id: str,
        dataset_ref: str,
        branch: str,
        schema_hash: str,
        staged_files: Sequence[DatasetStagedFile],
        created_at: str,
        sort_order: Sequence[str] | None,
    ) -> StoredDatasetCommit:
        pa = import_module("pyarrow")
        pq = import_module("pyarrow.parquet")
        tables = [_iceberg_compatible_arrow_table(pq.read_table(str(staged.path))) for staged in staged_files]
        arrow_schema = pa.concat_tables(tables).schema
        self._load_or_create_table(identifier, arrow_schema)
        self._evolve_schema_if_needed(identifier, arrow_schema)
        table = self._reload(identifier)
        uploaded = self._upload_iceberg_data_files(table, version_id, staged_files, tables, sort_order)
        props = _snapshot_properties(version_id, dataset_ref, branch, schema_hash, created_at)
        expressions = import_module("pyiceberg.expressions")
        with table.transaction() as tx:
            if table.current_snapshot() is not None:
                tx.delete(expressions.AlwaysTrue(), snapshot_properties=props)
            tx.add_files([file["uri"] for file in uploaded], snapshot_properties=props, check_duplicate_files=False)
        snapshot = self._reload(identifier).current_snapshot()
        if snapshot is None:
            raise ValueError("Iceberg add-files commit did not produce a snapshot")
        return self._stored_snapshot_commit(
            identifier, int(snapshot.snapshot_id), version_id, dataset_ref, branch, schema_hash, created_at, uploaded
        )

    def _upload_iceberg_data_files(
        self,
        table: Any,
        version_id: str,
        staged_files: Sequence[DatasetStagedFile],
        tables: Sequence[Any],
        sort_order: Sequence[str] | None,
    ) -> list[DatasetManifestFile]:
        pq = import_module("pyarrow.parquet")
        uploaded: list[DatasetManifestFile] = []
        for index, staged_file in enumerate(staged_files):
            local_path = self.cache_root / "prepared" / version_id / f"part-{index:05d}.parquet"
            local_path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(tables[index], str(local_path))
            uri = f"{table.location().rstrip('/')}/data/foundry-{version_id}-part-{index:05d}.parquet"
            self._write_table_file(table, local_path, uri)
            uploaded.append(self._uploaded_manifest_file(uri, local_path, staged_file, tables[index], sort_order))
        return uploaded

    def _write_table_file(self, table: Any, local_path: Path, uri: str) -> None:
        output = table.io.new_output(uri)
        with local_path.open("rb") as source, output.create(overwrite=True) as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)

    def _uploaded_manifest_file(
        self,
        uri: str,
        local_path: Path,
        staged_file: DatasetStagedFile,
        table: Any,
        sort_order: Sequence[str] | None,
    ) -> DatasetManifestFile:
        row_count = _uploaded_row_count(staged_file, int(table.num_rows))
        manifest_file: DatasetManifestFile = {
            "uri": uri,
            "format": "parquet",
            "row_count": row_count,
            "byte_size": local_path.stat().st_size,
            "content_hash": _path_sha256(local_path),
            "partition_values": dict(staged_file.partition_values or {}),
        }
        return parquet_manifest_file_metadata(manifest_file, local_path, sort_order=sort_order)

    def _stored_snapshot_commit(
        self,
        identifier: str,
        snapshot_id: int,
        version_id: str,
        dataset_ref: str,
        branch: str,
        schema_hash: str,
        created_at: str,
        expected_files: Sequence[DatasetManifestFile],
    ) -> StoredDatasetCommit:
        manifest_uri = self._manifest_uri(identifier, snapshot_id)
        table = self._load_table(identifier)
        files = self._manifest_files(table, snapshot_id, expected_files=_files_by_uri(expected_files))
        content_hash = _snapshot_token(files)
        manifest: DatasetManifest = _dataset_manifest(
            version_id, dataset_ref, branch, schema_hash, files, created_at, self.profile_name
        )
        self._write_sidecar_manifest(table, snapshot_id, manifest, content_hash)
        first_path = (
            self._download_snapshot_file(table, files[0])
            if files
            else self._materialize_snapshot(table, snapshot_id, identifier)
        )
        return StoredDatasetCommit(
            manifest_uri=manifest_uri,
            data_file_uri=files[0]["uri"] if files else manifest_uri,
            data_file_path=first_path,
            byte_size=sum(f["byte_size"] for f in files),
            content_hash=content_hash,
            manifest=manifest,
        )

    def _guard_version_not_committed(self, identifier: str, version_id: str, operation: str) -> None:
        """Guard version not committed."""
        if self._find_snapshot_id(identifier, version_id, operation) is not None:
            raise AdapterError(
                AdapterFailure(
                    self.profile_name,
                    operation,
                    "conflict",
                    False,
                    f"Iceberg dataset version already committed: {version_id}",
                    idempotency_key=version_id,
                )
            )

    def _merged_staged_file(self, version_id: str, staged_files: Sequence[DatasetStagedFile]) -> Path:
        """Return one parquet payload for an atomic Iceberg snapshot commit."""
        if len(staged_files) == 1:
            return staged_files[0].path
        pa = import_module("pyarrow")
        pq = import_module("pyarrow.parquet")
        tables = [pq.read_table(str(staged_file.path)) for staged_file in staged_files]
        target = self.cache_root / "merged" / f"{version_id}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.concat_tables(tables), str(target))
        return target

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
        sort_order: Sequence[str] | None = None,
    ) -> list[DatasetManifestFile]:
        """Manifest files."""
        files: list[DatasetManifestFile] = []
        actual_uris: set[str] = set()
        for task in table.scan(snapshot_id=snapshot_id).plan_files():
            data = task.file
            uri = str(data.file_path)
            expected = expected_files.get(uri) if expected_files is not None else None
            actual_uris.add(uri)
            files.append(self._manifest_file_entry(table, data, uri, expected, sort_order))
        if expected_files is not None and set(expected_files) != actual_uris:
            raise ValueError("Iceberg snapshot file list differs from the committed Foundry manifest")
        return files

    def _manifest_file_entry(
        self,
        table: Any,
        data: Any,
        uri: str,
        expected: DatasetManifestFile | None,
        sort_order: Sequence[str] | None,
    ) -> DatasetManifestFile:
        content_hash = self._hash_iceberg_file(table, uri)
        if expected is not None:
            self._verify_manifest_file(uri, data, content_hash, expected)
        entry: DatasetManifestFile = {
            "uri": uri,
            "format": "parquet",
            "row_count": int(data.record_count),
            "byte_size": int(data.file_size_in_bytes),
            "content_hash": content_hash,
        }
        _copy_expected_manifest_metadata(entry, expected)
        if "column_stats" in entry:
            return entry
        return parquet_manifest_file_metadata(
            entry,
            self._download_snapshot_file(table, entry),
            sort_order=sort_order,
        )

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

    def _rewrite_current_snapshot_if_needed(
        self,
        table: Any,
        identifier: str,
        dataset_ref: str,
        branch: str,
        plan: IcebergMaintenancePlan,
    ) -> _MaintenanceRewriteResult:
        """Compact the current snapshot when the maintenance plan requires it."""
        current = table.current_snapshot()
        candidate = _current_compaction_candidate(plan)
        if current is None or candidate is None:
            return _empty_rewrite_result(plan["current_snapshot_id"])
        before_id = int(current.snapshot_id)
        before_arrow = _iceberg_compatible_arrow_table(table.scan(snapshot_id=before_id).to_arrow())
        before_hash = _arrow_table_row_hash(before_arrow)
        props = _maintenance_snapshot_properties(current, dataset_ref, branch, before_id)
        try:
            table.overwrite(before_arrow, snapshot_properties=props)
        except Exception as exc:  # noqa: BLE001 - classified by the adapter contract
            raise self._adapter_error("run_iceberg_maintenance", exc) from exc
        return self._verify_compacted_snapshot(
            identifier,
            dataset_ref,
            branch,
            before_id=before_id,
            before_hash=before_hash,
            before_file_count=candidate["file_count"],
        )

    def _verify_compacted_snapshot(
        self,
        identifier: str,
        dataset_ref: str,
        branch: str,
        *,
        before_id: int,
        before_hash: str,
        before_file_count: int,
    ) -> _MaintenanceRewriteResult:
        table = self._load_table(identifier)
        after = table.current_snapshot()
        if after is None:
            raise ValueError("Iceberg maintenance rewrite did not produce a snapshot")
        after_id = int(after.snapshot_id)
        after_arrow = table.scan(snapshot_id=after_id).to_arrow()
        after_hash = _arrow_table_row_hash(after_arrow)
        if before_hash != after_hash:
            self._rollback_current_snapshot(identifier, before_id)
            raise ValueError("Iceberg maintenance rewrite changed row content")
        output_file_count = len(self._write_maintenance_sidecar(table, after_id, dataset_ref, branch, after_arrow))
        return _rewrite_result(before_id, after_id, before_file_count, output_file_count, before_hash, after_hash)

    def _write_maintenance_sidecar(
        self,
        table: Any,
        snapshot_id: int,
        dataset_ref: str,
        branch: str,
        arrow_table: Any,
    ) -> list[DatasetManifestFile]:
        files = self._manifest_files(table, snapshot_id)
        content_hash = _snapshot_token(files)
        manifest = _dataset_manifest(
            f"maintenance:{snapshot_id}",
            dataset_ref,
            branch,
            _arrow_schema_hash(arrow_table),
            files,
            _utc_now_iso(),
            self.profile_name,
        )
        self._write_sidecar_manifest(table, snapshot_id, manifest, content_hash)
        return files

    def _rollback_current_snapshot(self, identifier: str, snapshot_id: int) -> None:
        try:
            self._load_table(identifier).manage_snapshots().rollback_to_snapshot(snapshot_id).commit()
        except Exception as exc:  # noqa: BLE001 - best effort rollback is still classified
            raise self._adapter_error("run_iceberg_maintenance", exc) from exc

    def _expire_deletable_snapshots(
        self,
        identifier: str,
        plan: IcebergMaintenancePlan,
    ) -> tuple[list[int], list[int], list[str]]:
        """Expire plan-deletable snapshots and clean unreachable files."""
        snapshot_ids = list(plan["deletable_snapshot_ids"])
        if not snapshot_ids:
            return [], [], []
        table = self._load_table(identifier)
        cleanup_targets = self._snapshot_cleanup_targets(table, snapshot_ids)
        try:
            table.maintenance.expire_snapshots().by_ids(snapshot_ids).commit()
        except Exception as exc:  # noqa: BLE001 - classified by the adapter contract
            raise self._adapter_error("run_iceberg_maintenance", exc) from exc
        after_table = self._load_table(identifier)
        expired = [snapshot_id for snapshot_id in snapshot_ids if after_table.snapshot_by_id(snapshot_id) is None]
        skipped = [snapshot_id for snapshot_id in snapshot_ids if snapshot_id not in expired]
        cleaned = self._delete_expired_snapshot_files(after_table, cleanup_targets, expired)
        return expired, skipped, cleaned

    def _snapshot_cleanup_targets(self, table: Any, snapshot_ids: list[int]) -> dict[int, set[str]]:
        targets: dict[int, set[str]] = {}
        for snapshot_id in snapshot_ids:
            if table.snapshot_by_id(snapshot_id) is None:
                continue
            data_uris, _ = self._snapshot_data_file_uris(table, snapshot_id)
            targets[snapshot_id] = set(data_uris) | {self._sidecar_manifest_location(table, snapshot_id)}
        return targets

    def _delete_expired_snapshot_files(
        self,
        table: Any,
        targets: Mapping[int, set[str]],
        expired_snapshot_ids: list[int],
    ) -> list[str]:
        active = self._active_snapshot_file_uris(table)
        deleted: list[str] = []
        for snapshot_id in expired_snapshot_ids:
            for uri in sorted(targets.get(snapshot_id, set()) - active):
                if self._delete_table_file(table, uri):
                    deleted.append(uri)
        return deleted

    def _active_snapshot_file_uris(self, table: Any) -> set[str]:
        active: set[str] = set()
        for snapshot in table.snapshots():
            snapshot_id = int(snapshot.snapshot_id)
            data_uris, _ = self._snapshot_data_file_uris(table, snapshot_id)
            active.update(data_uris)
            active.add(self._sidecar_manifest_location(table, snapshot_id))
        return active

    def _delete_table_file(self, table: Any, uri: str) -> bool:
        try:
            table.io.delete(uri)
        except FileNotFoundError:
            return False
        except Exception as exc:  # noqa: BLE001 - classified by the adapter contract
            raise self._adapter_error("run_iceberg_maintenance", exc) from exc
        return True

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

    def _find_snapshot_id(self, identifier: str, version_id: str, operation: str = "commit_staged_file") -> int | None:
        """Find snapshot id."""
        try:
            table = self._catalog.load_table(identifier)
        except Exception as exc:
            if _is_missing_table(exc):
                return None
            if _is_corrupt_metadata(exc):
                raise ValueError(f"Iceberg table metadata is corrupt: {identifier}: {exc}") from exc
            raise self._adapter_error(operation, exc, idempotency_key=version_id) from exc
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
