from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import pytest
from foundry_lite.application.ports import (
    DatasetFileRow,
    DatasetManifestFile,
    DatasetRow,
    DatasetSchemaRow,
    DatasetStagedFile,
    DatasetTransactionRow,
    DatasetVersionRow,
)
from foundry_lite.application.services import pipeline_dataset_version_reader
from foundry_lite.application.services.pipeline_dataset_version_read_contracts import (
    ExactDatasetVersionReadFailed,
    ExactDatasetVersionReadRequest,
    ResolvedExactDatasetVersion,
    dataset_version_content_fingerprint,
)
from foundry_lite.application.services.pipeline_dataset_version_read_validation import (
    validate_exact_dataset_manifest,
    validate_exact_dataset_metadata,
    validate_exact_dataset_request,
    validate_exact_dataset_rows,
    validated_manifest_files,
)
from foundry_lite.application.services.pipeline_dataset_version_reader import (
    ExactCommittedDatasetVersionReader,
)
from foundry_lite.application.services.pipeline_source_contract_builders import (
    build_dataset_source_contract,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2SourceContract,
    PipelineV2SourceVersion,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure.adapters import (
    DuckDBComputeAdapter,
    LocalDatasetStorageAdapter,
)
from foundry_lite.security.policy import PolicyService

_CTX = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="user-data-engineer",
    request_id="req-exact-version",
    roles=("data_engineer",),
)


class _TransactionManager:
    @contextmanager
    def begin(self) -> Any:
        yield object()


@dataclass
class _DatasetRepository:
    row: DatasetRow | None
    requested_tenants: list[str]

    def dataset_by_id(
        self,
        *,
        transaction: object,
        tenant_id: str,
        dataset_id: str,
    ) -> DatasetRow | None:
        del transaction
        self.requested_tenants.append(tenant_id)
        if self.row is None or self.row["id"] != dataset_id or self.row["tenant_id"] != tenant_id:
            return None
        return cast(DatasetRow, dict(self.row))


@dataclass
class _DatasetVersionRepository:
    row: DatasetVersionRow | None
    exact_lookups: list[str]

    def version_by_dataset_id_and_id(
        self,
        *,
        transaction: object,
        dataset_id: str,
        version_id: str,
    ) -> DatasetVersionRow | None:
        del transaction
        self.exact_lookups.append(version_id)
        if self.row is None or self.row["dataset_id"] != dataset_id or self.row["id"] != version_id:
            return None
        return cast(DatasetVersionRow, dict(self.row))

    def latest_version_by_dataset_id(self, **_kwargs: object) -> DatasetVersionRow | None:
        raise AssertionError("exact reader must never resolve latest")


@dataclass
class _DatasetQualityRepository:
    row: DatasetSchemaRow | None

    def schema_by_version(
        self,
        *,
        transaction: object,
        dataset_id: str,
        schema_version: int,
    ) -> DatasetSchemaRow | None:
        del transaction
        if self.row is None or self.row["dataset_id"] != dataset_id or self.row["version"] != schema_version:
            return None
        return cast(DatasetSchemaRow, dict(self.row))


@dataclass
class _DatasetTransactionRepository:
    transaction_row: DatasetTransactionRow | None
    file_rows: list[DatasetFileRow]
    requested_tenants: list[str]

    def committed_transaction_by_version(
        self,
        *,
        transaction: object,
        tenant_id: str,
        committed_version_id: str,
    ) -> DatasetTransactionRow | None:
        del transaction
        self.requested_tenants.append(tenant_id)
        row = self.transaction_row
        if row is None or row["tenant_id"] != tenant_id or row["committed_version_id"] != committed_version_id:
            return None
        return cast(DatasetTransactionRow, dict(row))

    def files_for_version(
        self,
        *,
        transaction: object,
        tenant_id: str,
        dataset_version_id: str,
    ) -> list[DatasetFileRow]:
        del transaction
        self.requested_tenants.append(tenant_id)
        return [
            cast(DatasetFileRow, dict(row))
            for row in self.file_rows
            if row["tenant_id"] == tenant_id and row["dataset_version_id"] == dataset_version_id
        ]


class _RecordingComputeAdapter:
    def __init__(self) -> None:
        self.delegate = DuckDBComputeAdapter()
        self.read_paths: list[Path] = []

    def rows_from_parquet(self, parquet_path: Path) -> list[dict[str, object]]:
        self.read_paths.append(parquet_path)
        return self.delegate.rows_from_parquet(parquet_path)

    def rows_from_parquet_bounded(
        self,
        parquet_path: Path,
        *,
        max_rows: int,
        max_decoded_bytes: int,
    ):
        self.read_paths.append(parquet_path)
        return self.delegate.rows_from_parquet_bounded(
            parquet_path,
            max_rows=max_rows,
            max_decoded_bytes=max_decoded_bytes,
        )


@dataclass
class _ReaderHarness:
    reader: ExactCommittedDatasetVersionReader
    request: ExactDatasetVersionReadRequest
    storage: LocalDatasetStorageAdapter
    compute: _RecordingComputeAdapter
    version: DatasetVersionRow
    transaction_repository: _DatasetTransactionRepository


def test_exact_committed_dataset_version_reader_reads_every_manifest_file_in_order(
    tmp_path: Path,
) -> None:
    harness = _reader_harness(tmp_path)
    orphan = harness.storage.root / "orphan.parquet"
    DuckDBComputeAdapter().rows_to_parquet([{"sequence": 999}], orphan, ["sequence"])

    result = harness.reader.read(_CTX, request=harness.request)

    assert [row["sequence"] for row in result.rows] == [1, 2, 3]
    assert [path.name for path in harness.compute.read_paths] == ["part-00000.parquet", "part-00001.parquet"]
    assert result.schema_contract["columns"] == ({"name": "sequence", "type": "integer"},)
    assert result.security_envelope["classification"] == "CONFIDENTIAL"
    assert result.access_evidence["tenantId"] == "tenant-demo"
    assert result.artifact_ref()["versionId"] == "dsv-orders-v1"
    assert result.runtime_manifest()["fileCount"] == 2
    assert harness.transaction_repository.requested_tenants == ["tenant-demo", "tenant-demo"]


def test_exact_committed_dataset_version_reader_rejects_uncommitted_version(
    tmp_path: Path,
) -> None:
    harness = _reader_harness(tmp_path)
    harness.transaction_repository.transaction_row = None

    with pytest.raises(ExactDatasetVersionReadFailed) as raised:
        harness.reader.read(_CTX, request=harness.request)

    assert raised.value.details["reason"] == "source_version_not_committed"
    assert harness.compute.read_paths == []


@pytest.mark.parametrize(
    ("limit_name", "reason"),
    [
        ("MAX_PIPELINE_DATASET_SOURCE_ROWS", "source_row_limit_exceeded"),
        ("MAX_PIPELINE_DATASET_SOURCE_BYTES", "source_byte_limit_exceeded"),
    ],
)
def test_exact_committed_dataset_version_reader_rejects_oversized_source_before_file_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    reason: str,
) -> None:
    harness = _reader_harness(tmp_path)
    monkeypatch.setattr(pipeline_dataset_version_reader, limit_name, 1)

    with pytest.raises(ExactDatasetVersionReadFailed) as raised:
        harness.reader.read(_CTX, request=harness.request)

    assert raised.value.details["reason"] == reason
    assert harness.compute.read_paths == []


def test_exact_committed_dataset_version_reader_maps_decoded_parquet_limit_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _reader_harness(tmp_path)

    def reject_decode(*_args: object, **_kwargs: object) -> None:
        from foundry_lite.domain.errors import ValidationFailed

        raise ValidationFailed(
            "parquet read bound exceeded",
            details={"limitKind": "decoded_bytes", "actual": 101, "maximum": 100},
        )

    monkeypatch.setattr(harness.compute, "rows_from_parquet_bounded", reject_decode)

    with pytest.raises(ExactDatasetVersionReadFailed) as raised:
        harness.reader.read(_CTX, request=harness.request)

    assert raised.value.details["reason"] == "source_decoded_limit_exceeded"
    assert raised.value.details["readError"]["limitKind"] == "decoded_bytes"


def test_exact_committed_dataset_version_reader_rejects_manifest_registry_tampering(
    tmp_path: Path,
) -> None:
    harness = _reader_harness(tmp_path)
    manifest_path = Path(harness.version["manifest_uri"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["content_hash"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ExactDatasetVersionReadFailed) as raised:
        harness.reader.read(_CTX, request=harness.request)

    assert raised.value.details["reason"] == "source_manifest_file_registry_mismatch"
    assert harness.compute.read_paths == []


def test_exact_committed_dataset_version_reader_rejects_same_size_file_tampering(
    tmp_path: Path,
) -> None:
    harness = _reader_harness(tmp_path)
    target = harness.storage.data_file_paths(harness.version["manifest_uri"])[0]
    target.write_bytes(b"x" * target.stat().st_size)

    with pytest.raises(ExactDatasetVersionReadFailed) as raised:
        harness.reader.read(_CTX, request=harness.request)

    assert raised.value.details["reason"] == "source_storage_inconsistent"
    assert harness.compute.read_paths == []


def test_exact_committed_dataset_version_reader_rejects_missing_manifest_file(
    tmp_path: Path,
) -> None:
    harness = _reader_harness(tmp_path)
    target = harness.storage.data_file_paths(harness.version["manifest_uri"])[0]
    target.unlink()

    with pytest.raises(ExactDatasetVersionReadFailed) as raised:
        harness.reader.read(_CTX, request=harness.request)

    assert raised.value.details["reason"] == "source_storage_inconsistent"
    storage_error = raised.value.details["storageError"]
    assert isinstance(storage_error, dict)
    assert storage_error["error_type"] == "committed_version_storage_missing"
    assert harness.compute.read_paths == []


def test_exact_committed_dataset_version_reader_rejects_stale_deployment_fingerprint(
    tmp_path: Path,
) -> None:
    harness = _reader_harness(tmp_path)
    request = replace(harness.request, content_fingerprint="f" * 64)

    with pytest.raises(ExactDatasetVersionReadFailed) as raised:
        harness.reader.read(_CTX, request=request)

    assert raised.value.details["reason"] == "source_version_fingerprint_mismatch"
    assert harness.compute.read_paths == []


def test_exact_committed_dataset_version_reader_rejects_weakened_security_pin(
    tmp_path: Path,
) -> None:
    harness = _reader_harness(tmp_path)
    envelope = {**harness.request.security_envelope, "classification": "PUBLIC"}
    request = replace(harness.request, security_envelope=envelope)

    with pytest.raises(ExactDatasetVersionReadFailed) as raised:
        harness.reader.read(_CTX, request=request)

    assert raised.value.details["reason"] == "source_security_classification_weakened"
    assert harness.compute.read_paths == []


def test_dataset_reader_fingerprint_matches_source_contract_builder(
    tmp_path: Path,
) -> None:
    harness = _reader_harness(tmp_path)
    dataset = _dataset_row()
    schema = _schema_row()
    contract = build_dataset_source_contract(
        node={
            "id": "source-orders",
            "kind": "source",
            "descriptorId": "source.dataset",
            "specVersion": 1,
            "config": {},
        },
        dataset_ref="raw.orders",
        dataset=dataset,  # type: ignore[arg-type]
        version=harness.version,
        schema=schema,
        ctx=_CTX,
    )

    assert contract.version_pins[0].content_fingerprint == dataset_version_content_fingerprint(
        harness.version,
        schema["schema_hash"],
    )


def test_exact_dataset_request_validation_rejects_every_invalid_coordinate() -> None:
    request = _exact_read_request("manifest.json", 42)
    cases = (
        ("source_artifact_kind_not_dataset_version", replace(request, artifact_kind="table")),
        ("source_coordinates_invalid", replace(request, dataset_ref=" ")),
        ("source_version_number_invalid", replace(request, version_number=0)),
    )

    for reason, invalid in cases:
        with pytest.raises(ExactDatasetVersionReadFailed) as raised:
            validate_exact_dataset_request(invalid)
        assert raised.value.details["reason"] == reason


def test_exact_dataset_metadata_validation_rejects_all_pinned_truth_drift() -> None:
    request = _exact_read_request("manifest.json", 42)
    dataset = _dataset_row()
    version = _version_row("manifest.json", 42)
    schema = _schema_row()
    transaction = _transaction_row()
    valid = ResolvedExactDatasetVersion(dataset, version, schema, transaction, ())
    cases = (
        ("source_dataset_tenant_mismatch", {"dataset": {**dataset, "tenant_id": "other"}}),
        ("source_dataset_ref_mismatch", {"dataset": {**dataset, "name": "other"}}),
        ("source_dataset_not_active", {"dataset": {**dataset, "status": "disabled"}}),
        ("source_version_tenant_mismatch", {"version": {**version, "tenant_id": "other"}}),
        ("source_version_id_mismatch", {"version": {**version, "id": "other"}}),
        ("source_version_not_committed", {"version": {**version, "status": "OPEN"}}),
        (
            "source_commit_transaction_mismatch",
            {"transaction": {**transaction, "branch": "staging"}},
        ),
        (
            "source_commit_transaction_id_mismatch",
            {"transaction": {**transaction, "id": "other"}},
        ),
        ("source_schema_version_mismatch", {"schema": {**schema, "version": 2}}),
        ("source_schema_invalid", {"schema": {**schema, "schema_json": "invalid"}}),
        ("source_schema_pin_mismatch", {"request": replace(request, schema_hash="other")}),
        (
            "source_schema_contract_mismatch",
            {"request": replace(request, schema_contract={"columns": []})},
        ),
        (
            "source_version_number_mismatch",
            {"request": replace(request, version_number=2)},
        ),
        (
            "source_version_metadata_mismatch",
            {"request": replace(request, version_metadata={**request.version_metadata, "branch": "dev"})},
        ),
        (
            "source_security_envelope_invalid",
            {
                "request": replace(
                    request,
                    security_envelope={**request.security_envelope, "inheritance": "copy"},
                )
            },
        ),
        (
            "source_security_owner_mismatch",
            {
                "request": replace(
                    request,
                    security_envelope={**request.security_envelope, "ownerTeam": "other"},
                )
            },
        ),
        (
            "source_access_evidence_invalid",
            {
                "request": replace(
                    request,
                    access_evidence={**request.access_evidence, "permission": "dataset:write"},
                )
            },
        ),
        (
            "source_access_scope_invalid",
            {
                "request": replace(
                    request,
                    access_evidence={**request.access_evidence, "scopeEnforcement": "global"},
                )
            },
        ),
    )

    for reason, changes in cases:
        candidate_request = cast(ExactDatasetVersionReadRequest, changes.get("request", request))
        candidate = ResolvedExactDatasetVersion(
            cast(DatasetRow, changes.get("dataset", valid.dataset)),
            cast(DatasetVersionRow, changes.get("version", valid.version)),
            cast(DatasetSchemaRow, changes.get("schema", valid.schema)),
            cast(DatasetTransactionRow, changes.get("transaction", valid.transaction)),
            (),
        )
        with pytest.raises(ExactDatasetVersionReadFailed) as raised:
            validate_exact_dataset_metadata(_CTX, candidate_request, candidate)
        assert raised.value.details["reason"] == reason


def test_exact_dataset_manifest_validation_rejects_all_integrity_drift() -> None:
    request = _exact_read_request("manifest.json", 42)
    version = _version_row("manifest.json", 42)
    schema = _schema_row()
    manifest_file: DatasetManifestFile = {
        "uri": "part.parquet",
        "format": "parquet",
        "row_count": 3,
        "byte_size": 42,
        "content_hash": "a" * 64,
        "partition_values": {},
    }
    registered = _file_row(version["id"], 0, manifest_file)
    resolved = ResolvedExactDatasetVersion(
        _dataset_row(),
        version,
        schema,
        _transaction_row(),
        (registered,),
    )
    valid_manifest = {
        "version_id": request.version_id,
        "dataset": request.dataset_ref,
        "branch": version["branch"],
        "schema_hash": schema["schema_hash"],
        "storage_profile": "local",
        "files": [manifest_file],
    }
    invalid_files = (
        ("source_manifest_has_no_files", []),
        ("source_manifest_has_duplicate_files", [manifest_file, manifest_file]),
        ("source_manifest_file_format_unsupported", [{**manifest_file, "format": "csv"}]),
        ("source_manifest_file_counts_invalid", [{**manifest_file, "row_count": True}]),
        ("source_manifest_file_counts_invalid", [{**manifest_file, "byte_size": 0}]),
        ("source_manifest_file_uri_invalid", [{**manifest_file, "uri": " "}]),
        ("source_manifest_file_hash_invalid", [{**manifest_file, "content_hash": "bad"}]),
    )

    with pytest.raises(ExactDatasetVersionReadFailed) as raised:
        validate_exact_dataset_manifest(
            request,
            resolved,
            {**valid_manifest, "branch": "wrong"},
            "local",
        )
    assert raised.value.details["reason"] == "source_manifest_coordinates_mismatch"

    for reason, files in invalid_files:
        with pytest.raises(ExactDatasetVersionReadFailed) as raised:
            validated_manifest_files(request, {"files": files})
        assert raised.value.details["reason"] == reason

    with pytest.raises(ExactDatasetVersionReadFailed) as raised:
        validate_exact_dataset_manifest(
            request,
            resolved,
            {**valid_manifest, "files": [{**manifest_file, "row_count": 2}]},
            "local",
        )
    assert raised.value.details["reason"] == "source_manifest_totals_mismatch"

    with pytest.raises(ExactDatasetVersionReadFailed) as raised:
        validate_exact_dataset_rows(request, version, valid_manifest, [{"sequence": 1}])
    assert raised.value.details["reason"] == "source_rows_count_mismatch"


def test_exact_dataset_unknown_classification_must_match_exactly() -> None:
    request = _exact_read_request("manifest.json", 42)
    dataset = cast(DatasetRow, {**_dataset_row(), "classification": "CUSTOM"})
    request = replace(
        request,
        security_envelope={**request.security_envelope, "classification": "OTHER"},
    )
    resolved = ResolvedExactDatasetVersion(
        dataset,
        _version_row("manifest.json", 42),
        _schema_row(),
        _transaction_row(),
        (),
    )

    with pytest.raises(ExactDatasetVersionReadFailed) as raised:
        validate_exact_dataset_metadata(_CTX, request, resolved)

    assert raised.value.details["reason"] == "source_security_classification_weakened"


def _exact_read_request(
    manifest_uri: str,
    byte_size: int,
) -> ExactDatasetVersionReadRequest:
    version = _version_row(manifest_uri, byte_size)
    schema = _schema_row()
    return ExactDatasetVersionReadRequest.from_source_contract(_source_contract(version, schema))


def _reader_harness(tmp_path: Path) -> _ReaderHarness:
    storage = LocalDatasetStorageAdapter(tmp_path / "dataset-storage")
    stored = _commit_two_parts(storage)
    version = _version_row(stored.manifest_uri, stored.byte_size)
    schema = _schema_row()
    files = [_file_row(version["id"], index, file) for index, file in enumerate(stored.manifest["files"])]
    transaction_repository = _DatasetTransactionRepository(_transaction_row(), files, [])
    compute = _RecordingComputeAdapter()
    reader = ExactCommittedDatasetVersionReader(
        engine=_TransactionManager(),
        policy=PolicyService(),
        dataset_repository=_DatasetRepository(_dataset_row(), []),
        dataset_version_repository=_DatasetVersionRepository(version, []),
        dataset_quality_repository=_DatasetQualityRepository(schema),
        dataset_transaction_repository=transaction_repository,
        dataset_storage=storage,
        compute_adapter=compute,
    )
    contract = _source_contract(version, schema)
    return _ReaderHarness(
        reader=reader,
        request=ExactDatasetVersionReadRequest.from_source_contract(contract),
        storage=storage,
        compute=compute,
        version=version,
        transaction_repository=transaction_repository,
    )


def _commit_two_parts(storage: LocalDatasetStorageAdapter) -> Any:
    compute = DuckDBComputeAdapter()
    first = storage.staging_file(
        tenant_id="tenant-demo",
        dataset_id="ds-orders",
        transaction_id="dstx-orders-v1",
        file_name="first.parquet",
    )
    second = storage.staging_file(
        tenant_id="tenant-demo",
        dataset_id="ds-orders",
        transaction_id="dstx-orders-v1",
        file_name="second.parquet",
    )
    compute.rows_to_parquet([{"sequence": 1}, {"sequence": 2}], first, ["sequence"])
    compute.rows_to_parquet([{"sequence": 3}], second, ["sequence"])
    return storage.commit_staged_files(
        tenant_id="tenant-demo",
        dataset_id="ds-orders",
        branch="main",
        version_id="dsv-orders-v1",
        dataset_ref="raw.orders",
        schema_hash="schema-orders-v1",
        staged_files=(
            DatasetStagedFile(first, row_count=2),
            DatasetStagedFile(second, row_count=1),
        ),
        row_count=3,
        created_at="2026-07-17T00:00:00Z",
    )


def _dataset_row() -> DatasetRow:
    return {
        "id": "ds-orders",
        "tenant_id": "tenant-demo",
        "namespace": "raw",
        "name": "orders",
        "description": None,
        "storage_kind": "managed",
        "storage_uri": None,
        "owner_team": "finance-data",
        "classification": "CONFIDENTIAL",
        "status": "active",
        "primary_key": [],
        "partition_spec": [],
        "sort_order": [],
        "target_file_size_bytes": None,
        "created_at": "2026-07-17T00:00:00Z",
        "updated_at": "2026-07-17T00:00:00Z",
    }


def _version_row(manifest_uri: str, byte_size: int) -> DatasetVersionRow:
    return {
        "id": "dsv-orders-v1",
        "tenant_id": "tenant-demo",
        "dataset_id": "ds-orders",
        "branch": "main",
        "version_number": 1,
        "transaction_id": "dstx-orders-v1",
        "schema_version": 1,
        "manifest_uri": manifest_uri,
        "row_count": 3,
        "byte_size": byte_size,
        "status": "active",
        "superseded_by_version_id": None,
        "created_at": "2026-07-17T00:00:00Z",
    }


def _schema_row() -> DatasetSchemaRow:
    return {
        "id": "dss-orders-v1",
        "dataset_id": "ds-orders",
        "version": 1,
        "schema_json": {"columns": [{"name": "sequence", "type": "integer"}]},
        "schema_hash": "schema-orders-v1",
        "created_at": "2026-07-17T00:00:00Z",
    }


def _transaction_row() -> DatasetTransactionRow:
    return {
        "id": "dstx-orders-v1",
        "tenant_id": "tenant-demo",
        "dataset_id": "ds-orders",
        "branch": "main",
        "tx_type": "APPEND",
        "status": "COMMITTED",
        "base_version_id": None,
        "committed_version_id": "dsv-orders-v1",
        "schema_version": 1,
        "created_by": "user-data-engineer",
        "created_at": "2026-07-17T00:00:00Z",
        "committed_at": "2026-07-17T00:01:00Z",
        "metadata": {},
    }


def _file_row(
    version_id: str,
    index: int,
    manifest_file: DatasetManifestFile,
) -> DatasetFileRow:
    return {
        "id": f"dsf-orders-{index}",
        "tenant_id": "tenant-demo",
        "dataset_version_id": version_id,
        "uri": manifest_file["uri"],
        "format": manifest_file["format"],
        "row_count": manifest_file["row_count"],
        "byte_size": manifest_file["byte_size"],
        "content_hash": manifest_file["content_hash"],
        "partition_values": manifest_file.get("partition_values") or {},
    }


def _source_contract(
    version: DatasetVersionRow,
    schema: DatasetSchemaRow,
) -> PipelineV2SourceContract:
    return PipelineV2SourceContract(
        node_id="source-orders",
        descriptor_id="source.dataset",
        artifact_kind="dataset_version",
        resource_ref="raw.orders",
        source_id="ds-orders",
        schema_contract=schema["schema_json"],
        schema_hash=schema["schema_hash"],
        schema_version=schema["version"],
        version_pins=(
            PipelineV2SourceVersion(
                version_id=version["id"],
                ordinal=version["version_number"],
                content_fingerprint=dataset_version_content_fingerprint(version, schema["schema_hash"]),
                metadata={
                    "versionNumber": version["version_number"],
                    "branch": version["branch"],
                    "manifestUri": version["manifest_uri"],
                    "rowCount": version["row_count"],
                    "byteSize": version["byte_size"],
                    "status": version["status"],
                    "schemaVersion": version["schema_version"],
                },
            ),
        ),
        security_envelope={
            "tenantId": "tenant-demo",
            "classification": "CONFIDENTIAL",
            "ownerTeam": "finance-data",
            "inheritance": "source",
        },
        access_evidence={
            "tenantId": "tenant-demo",
            "principalId": "user-data-engineer",
            "requestId": "req-deployment",
            "permission": "dataset:read",
            "scopeEnforcement": "tenant_scoped_repository",
        },
    )
