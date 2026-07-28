"""Fail-closed row reader for one exact committed Pipeline Builder Dataset source."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from foundry_lite.application.ports import (
    ComputeAdapter,
    DatasetManifest,
    DatasetQualityRepository,
    DatasetRepository,
    DatasetStorageAdapter,
    DatasetTransactionRepository,
    DatasetVersionRepository,
    DatasetVersionRow,
    TabularRow,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.serving_security import (
    require_dataset_serving_access,
)
from foundry_lite.application.services.dataset.storage_consistency import (
    committed_manifest,
    committed_version_file_paths,
)
from foundry_lite.application.services.pipeline_dataset_version_read_contracts import (
    ExactDatasetVersionReadRequest,
    ExactDatasetVersionReadResult,
    ResolvedExactDatasetVersion,
    exact_dataset_result,
    exact_dataset_version_request_failure,
)
from foundry_lite.application.services.pipeline_dataset_version_read_validation import (
    validate_exact_dataset_manifest,
    validate_exact_dataset_metadata,
    validate_exact_dataset_request,
    validate_exact_dataset_rows,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation, ValidationFailed
from foundry_lite.security.policy import PolicyService

MAX_PIPELINE_DATASET_SOURCE_ROWS = 100_000
MAX_PIPELINE_DATASET_SOURCE_BYTES = 256 * 1024 * 1024


class ExactCommittedDatasetVersionReader(CoreService):
    """Read only a deployment-pinned committed version; never resolve ``latest``."""

    required_dependencies = (
        "engine",
        "policy",
        "dataset_repository",
        "dataset_version_repository",
        "dataset_quality_repository",
        "dataset_transaction_repository",
        "dataset_storage",
        "compute_adapter",
    )
    engine: TransactionManager
    policy: PolicyService
    dataset_repository: DatasetRepository
    dataset_version_repository: DatasetVersionRepository
    dataset_quality_repository: DatasetQualityRepository
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_storage: DatasetStorageAdapter
    compute_adapter: ComputeAdapter

    def read(
        self,
        ctx: RequestContext,
        *,
        request: ExactDatasetVersionReadRequest,
    ) -> ExactDatasetVersionReadResult:
        self.policy.require(ctx, "dataset:read")
        validate_exact_dataset_request(request)
        with self.engine.begin() as transaction:
            resolved = self._resolve_metadata(transaction, ctx, request)
        require_dataset_serving_access(
            ctx=ctx,
            policy=self.policy,
            classification=resolved.dataset["classification"],
            transaction_metadata=resolved.transaction["metadata"],
            version_id=str(resolved.version["id"]),
        )
        validate_exact_dataset_metadata(ctx, request, resolved)
        _require_materialization_bound(request, resolved.version)
        manifest = self._load_manifest(request, resolved.version)
        validate_exact_dataset_manifest(request, resolved, manifest, self.dataset_storage.profile_name)
        paths = self._file_paths(request, resolved.version)
        rows = self._read_rows(request, paths)
        validate_exact_dataset_rows(request, resolved.version, manifest, rows)
        return exact_dataset_result(ctx=ctx, request=request, resolved=resolved, manifest=manifest, rows=rows)

    def _resolve_metadata(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        request: ExactDatasetVersionReadRequest,
    ) -> ResolvedExactDatasetVersion:
        dataset = self.dataset_repository.dataset_by_id(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            dataset_id=request.dataset_id,
        )
        if dataset is None:
            raise exact_dataset_version_request_failure(request, "source_dataset_not_found")
        version = self.dataset_version_repository.version_by_dataset_id_and_id(
            transaction=transaction,
            dataset_id=request.dataset_id,
            version_id=request.version_id,
        )
        if version is None:
            raise exact_dataset_version_request_failure(request, "source_version_not_found")
        schema = self.dataset_quality_repository.schema_by_version(
            transaction=transaction,
            dataset_id=request.dataset_id,
            schema_version=version["schema_version"],
        )
        committed = self.dataset_transaction_repository.committed_transaction_by_version(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            committed_version_id=request.version_id,
        )
        if schema is None or committed is None:
            reason = "source_schema_not_found" if schema is None else "source_version_not_committed"
            raise exact_dataset_version_request_failure(request, reason)
        files = self.dataset_transaction_repository.files_for_version(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            dataset_version_id=request.version_id,
        )
        return ResolvedExactDatasetVersion(dataset, version, schema, committed, tuple(files))

    def _load_manifest(
        self,
        request: ExactDatasetVersionReadRequest,
        version: DatasetVersionRow,
    ) -> DatasetManifest:
        try:
            return committed_manifest(self.dataset_storage, version["manifest_uri"])
        except InvariantViolation as exc:
            raise _storage_failure(request, exc) from exc

    def _file_paths(
        self,
        request: ExactDatasetVersionReadRequest,
        version: DatasetVersionRow,
    ) -> list[Path]:
        try:
            return committed_version_file_paths(self.dataset_storage, version)
        except InvariantViolation as exc:
            raise _storage_failure(request, exc) from exc

    def _read_rows(
        self,
        request: ExactDatasetVersionReadRequest,
        paths: Sequence[Path],
    ) -> tuple[TabularRow, ...]:
        rows: list[TabularRow] = []
        decoded_byte_count = 0
        try:
            for path in paths:
                read = self.compute_adapter.rows_from_parquet_bounded(
                    path,
                    max_rows=MAX_PIPELINE_DATASET_SOURCE_ROWS - len(rows),
                    max_decoded_bytes=MAX_PIPELINE_DATASET_SOURCE_BYTES - decoded_byte_count,
                    allowed_nested_columns=_allowed_nested_columns(request),
                )
                rows.extend(read.rows)
                decoded_byte_count += read.decoded_byte_count
        except ValidationFailed as exc:
            reason = (
                "source_decoded_limit_exceeded"
                if exc.details.get("limitKind") in {"rows", "decoded_bytes"}
                else "source_rows_unreadable"
            )
            raise exact_dataset_version_request_failure(
                request,
                reason,
                readError=dict(exc.details),
            ) from exc
        except Exception as exc:
            raise exact_dataset_version_request_failure(
                request,
                "source_rows_unreadable",
                errorType=type(exc).__name__,
            ) from exc

        return tuple(dict(row) for row in rows)


def _allowed_nested_columns(request: ExactDatasetVersionReadRequest) -> tuple[str, ...]:
    spec = request.version_metadata.get("geospatialSpec")
    if not isinstance(spec, Mapping) or spec.get("encoding") != "geojson":
        return ()
    geometry_field = spec.get("geometryField")
    if not isinstance(geometry_field, str) or not geometry_field.strip():
        return ()
    return (geometry_field.strip(),)


def _require_materialization_bound(
    request: ExactDatasetVersionReadRequest,
    version: DatasetVersionRow,
) -> None:
    if version["row_count"] > MAX_PIPELINE_DATASET_SOURCE_ROWS:
        raise exact_dataset_version_request_failure(
            request,
            "source_row_limit_exceeded",
            actualRowCount=version["row_count"],
            maxRowCount=MAX_PIPELINE_DATASET_SOURCE_ROWS,
        )
    if version["byte_size"] > MAX_PIPELINE_DATASET_SOURCE_BYTES:
        raise exact_dataset_version_request_failure(
            request,
            "source_byte_limit_exceeded",
            actualByteSize=version["byte_size"],
            maxByteSize=MAX_PIPELINE_DATASET_SOURCE_BYTES,
        )


def _storage_failure(
    request: ExactDatasetVersionReadRequest,
    error: InvariantViolation,
) -> InvariantViolation:
    return exact_dataset_version_request_failure(
        request,
        "source_storage_inconsistent",
        storageError=dict(error.details),
    )
