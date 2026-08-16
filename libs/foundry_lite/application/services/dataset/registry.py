"""Application service helpers for registry workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from foundry_lite.application.ports import (
    DatasetAggregationResult,
    DatasetAlreadyExistsError,
    DatasetInspectionPayload,
    DatasetManifest,
    DatasetRow,
    DatasetVersionRow,
    TabularRow,
    TransactionContext,
)
from foundry_lite.application.ports.resource_catalog_repository import ResourceCatalogRepository
from foundry_lite.application.primitives import (
    _dataset_ref_parts,
    _new_id,
    _now,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.aggregation import (
    build_dataset_aggregation_plan,
    dataset_aggregation_result,
)
from foundry_lite.application.services.dataset.protocols import (
    DatasetRuntimeBoundary,
    DatasetTransactionManager,
    DatasetVersionLookup,
)
from foundry_lite.application.services.dataset.serving_projection import (
    project_dataset_serving_rows,
)
from foundry_lite.application.services.dataset.serving_security import (
    require_dataset_serving_access,
)
from foundry_lite.application.services.resource_catalog_auto_registration import upsert_work_product_resource
from foundry_lite.application.services.runtime_error_payloads import scrub_error_text
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    InvariantViolation,
    NotFound,
)


@dataclass(frozen=True)
class _DatasetCreateFields:
    dataset_id: str
    namespace: str
    name: str
    description: str | None
    storage_kind: str
    storage_uri: str
    owner_team: str | None
    classification: str | None
    primary_key: list[str]
    partition_spec: list[str]
    sort_order: list[str]
    target_file_size_bytes: int | None
    created_at: str


class DatasetRegistryService(CoreService):
    required_dependencies = (
        "engine",
        "policy",
        "compute_adapter",
        "dataset_repository",
        "dataset_transaction_repository",
        "dataset_version_repository",
        "dataset_storage",
        "resource_catalog_repository",
    )
    required_collaborators = (
        "dataset_transaction_service",
        "dataset_version_service",
        "runtime_service",
    )
    dataset_transaction_service: DatasetTransactionManager
    dataset_version_service: DatasetVersionLookup
    runtime_service: DatasetRuntimeBoundary
    resource_catalog_repository: ResourceCatalogRepository

    def create_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        primary_key: list[str] | None = None,
        storage_kind: str = "parquet_manifest",
        description: str | None = None,
        owner_team: str | None = None,
        classification: str | None = None,
        partition_spec: list[str] | None = None,
        sort_order: list[str] | None = None,
        target_file_size_bytes: int | None = None,
    ) -> DatasetRow:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "dataset:write", "dataset", dataset_ref)
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="create",
            resource_type="dataset",
            resource_id=dataset_ref,
        )
        fields = self._dataset_create_fields(
            ctx,
            dataset_ref,
            primary_key=primary_key,
            storage_kind=storage_kind,
            description=description,
            owner_team=owner_team,
            classification=classification,
            partition_spec=partition_spec,
            sort_order=sort_order,
            target_file_size_bytes=target_file_size_bytes,
        )
        with self.engine.begin() as conn:
            self._insert_dataset_record(conn, ctx, dataset_ref, fields)
            self._upsert_dataset_resource(conn, ctx, dataset_ref, fields)
            self._audit_dataset_created(conn, ctx, fields.dataset_id, dataset_ref)
        return self.get_dataset(dataset_ref, ctx=ctx)

    def _dataset_create_fields(
        self,
        ctx: RequestContext,
        dataset_ref: str,
        *,
        primary_key: list[str] | None,
        storage_kind: str,
        description: str | None,
        owner_team: str | None,
        classification: str | None,
        partition_spec: list[str] | None,
        sort_order: list[str] | None,
        target_file_size_bytes: int | None,
    ) -> _DatasetCreateFields:
        dataset_id = _new_id("ds")
        namespace, name = _dataset_ref_parts(dataset_ref)
        return _DatasetCreateFields(
            dataset_id=dataset_id,
            namespace=namespace,
            name=name,
            description=description,
            storage_kind=storage_kind,
            storage_uri=self.dataset_storage.dataset_uri(ctx.tenant_id, dataset_id),
            owner_team=owner_team,
            classification=classification,
            primary_key=primary_key or [],
            partition_spec=partition_spec or [],
            sort_order=sort_order or [],
            target_file_size_bytes=target_file_size_bytes,
            created_at=_now(),
        )

    def _audit_dataset_created(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset_id: str,
        dataset_ref: str,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="dataset.created",
            resource_type="dataset",
            resource_id=dataset_id,
            action="create",
            after_ref={"dataset_ref": dataset_ref},
        )

    def _insert_dataset_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset_ref: str,
        fields: _DatasetCreateFields,
    ) -> None:
        try:
            self.dataset_repository.create_dataset(
                transaction=conn,
                dataset_id=fields.dataset_id,
                tenant_id=ctx.tenant_id,
                namespace=fields.namespace,
                name=fields.name,
                description=fields.description,
                storage_kind=fields.storage_kind,
                storage_uri=fields.storage_uri,
                owner_team=fields.owner_team,
                classification=fields.classification,
                primary_key=fields.primary_key,
                partition_spec=fields.partition_spec,
                sort_order=fields.sort_order,
                target_file_size_bytes=fields.target_file_size_bytes,
                created_at=fields.created_at,
                updated_at=fields.created_at,
            )
        except DatasetAlreadyExistsError as exc:
            raise ConflictDetected(
                "dataset already exists in this tenant",
                details={"dataset_ref": dataset_ref},
            ) from exc

    def _upsert_dataset_resource(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        dataset_ref: str,
        fields: _DatasetCreateFields,
    ) -> None:
        upsert_work_product_resource(
            conn=conn,
            ctx=ctx,
            repository=self.resource_catalog_repository,
            runtime_service=self.runtime_service,
            resource_type="dataset",
            display_name=dataset_ref,
            source_surface="dataset",
            source_ref=dataset_ref,
            operations_path=f"/datasets/{fields.namespace}/{fields.name}",
            metadata={"datasetId": fields.dataset_id, "classification": fields.classification or "public"},
            now=fields.created_at,
            idempotency_key=f"dataset-resource:{dataset_ref}",
            evidence={"datasetRef": dataset_ref},
        )

    def ensure_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        primary_key: list[str] | None = None,
        storage_kind: str = "parquet_manifest",
        classification: str | None = None,
        partition_spec: list[str] | None = None,
        sort_order: list[str] | None = None,
        target_file_size_bytes: int | None = None,
    ) -> DatasetRow:
        ctx = ctx or RequestContext()
        existing = self.find_dataset(dataset_ref, ctx=ctx)
        if existing is not None:
            return existing
        return self.create_dataset(
            dataset_ref,
            ctx=ctx,
            primary_key=primary_key,
            storage_kind=storage_kind,
            classification=classification,
            partition_spec=partition_spec,
            sort_order=sort_order,
            target_file_size_bytes=target_file_size_bytes,
        )

    def find_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> DatasetRow | None:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "dataset:read")
        dataset = self._find_dataset_row(dataset_ref, ctx=ctx)
        if dataset is not None:
            self.policy.require_dataset_classification(ctx, dataset["classification"])
        return dataset

    def _find_dataset_row(self, dataset_ref: str, *, ctx: RequestContext) -> DatasetRow | None:
        namespace, name = _dataset_ref_parts(dataset_ref)
        return self.dataset_repository.find_active_dataset(tenant_id=ctx.tenant_id, namespace=namespace, name=name)

    def get_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> DatasetRow:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "dataset:read")
        dataset = self.find_dataset(dataset_ref, ctx=ctx)
        if dataset is None:
            raise NotFound("dataset not found", details={"dataset_ref": dataset_ref})
        return dataset

    def list_datasets(self, *, ctx: RequestContext | None = None) -> list[DatasetRow]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "dataset:read")
        rows = self.dataset_repository.list_active_datasets(tenant_id=ctx.tenant_id)
        return [row for row in rows if self.policy.can_read_dataset_classification(ctx, row["classification"])]

    def list_dataset_versions(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[DatasetVersionRow]:
        ctx = ctx or RequestContext()
        dataset = self.get_dataset(dataset_ref, ctx=ctx)
        return self.dataset_version_repository.list_versions(dataset_id=dataset["id"])

    def preview_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        limit: int = 100,
        version: str = "latest",
        partition_filter: Mapping[str, object] | None = None,
    ) -> list[TabularRow]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "dataset:read")
        dataset = self.get_dataset(dataset_ref, ctx=ctx)
        version_row = self.dataset_version_service._get_version(dataset["id"], version, ctx=ctx)
        transaction_metadata = self._require_version_security_contract(
            ctx,
            version_row,
            dataset["classification"],
        )
        parquet_paths = self.dataset_transaction_service._version_preview_file_paths(
            version_row,
            partition_filter=partition_filter,
        )
        rows = self._preview_manifest_paths(parquet_paths, limit=int(limit))
        schema_row = self.dataset_version_service._schema_for_version(
            dataset["id"],
            version_row["schema_version"],
        )
        rows = project_dataset_serving_rows(
            rows,
            transaction_metadata,
            schema_row["schema_json"],
        )
        # A backing dataset must not leak a value that Object masking hides.
        return self.policy.mask_columns(ctx, rows)

    def aggregate_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        version: str = "latest",
        filters: Sequence[Mapping[str, object]] | None = None,
        group_by: Sequence[str] | None = None,
        select: Sequence[Mapping[str, object]] | None = None,
    ) -> DatasetAggregationResult:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "dataset:read")
        dataset = self.get_dataset(dataset_ref, ctx=ctx)
        version_row = self.dataset_version_service._get_version(dataset["id"], version, ctx=ctx)
        self._require_version_security_contract(ctx, version_row, dataset["classification"])
        schema_row = self.dataset_version_service._schema_for_version(dataset["id"], version_row["schema_version"])
        plan = build_dataset_aggregation_plan(
            dataset_ref,
            schema_row["schema_json"],
            self.policy.masked_column_names(ctx),
            filters=filters,
            group_by=group_by,
            select=select,
        )
        parquet_paths = self.dataset_transaction_service._version_preview_file_paths(version_row)
        compute_result = self.compute_adapter.aggregate_parquet(parquet_paths, plan)
        return dataset_aggregation_result(dataset_ref, str(version_row["id"]), compute_result)

    def _preview_manifest_paths(self, parquet_paths: Sequence[Path], *, limit: int) -> list[TabularRow]:
        rows: list[TabularRow] = []
        remaining = max(limit, 0)
        for parquet_path in parquet_paths:
            if remaining == 0:
                break
            batch = self.compute_adapter.preview_parquet(parquet_path, limit=remaining)
            rows.extend(batch)
            remaining -= len(batch)
        return rows

    def _committed_manifest_rows(self, version_row: DatasetVersionRow) -> list[TabularRow]:
        rows: list[TabularRow] = []
        parquet_paths = self.dataset_transaction_service._version_preview_file_paths(version_row)
        for parquet_path in parquet_paths:
            rows.extend(self.compute_adapter.rows_from_parquet(parquet_path))
        return rows

    def inspect_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        version: str = "latest",
    ) -> DatasetInspectionPayload:
        ctx = ctx or RequestContext()
        dataset = self.get_dataset(dataset_ref, ctx=ctx)
        version_row = self.dataset_version_service._get_version(dataset["id"], version, ctx=ctx)
        self._require_version_security_contract(ctx, version_row, dataset["classification"])
        schema_row = self.dataset_version_service._schema_for_version(dataset["id"], version_row["schema_version"])
        return {
            "dataset": dataset_ref,
            "dataset_id": dataset["id"],
            "version": self.dataset_transaction_service._current_view_version(version_row),
            "schema": schema_row["schema_json"],
            "manifest": self._inspect_manifest(dataset_ref, dataset, version_row),
        }

    def _inspect_manifest(
        self,
        dataset_ref: str,
        dataset: DatasetRow,
        version_row: DatasetVersionRow,
    ) -> DatasetManifest:
        try:
            return self.dataset_transaction_service._current_view_manifest(version_row)
        except InvariantViolation as exc:
            details = self._storage_error_details(dataset_ref, dataset, version_row, exc.details)
            raise InvariantViolation(scrub_error_text(exc.message), details=details) from exc

    def _require_version_security_contract(
        self,
        ctx: RequestContext,
        version: DatasetVersionRow,
        classification: object,
    ) -> Mapping[str, object]:
        with self.engine.begin() as transaction:
            row = self.dataset_transaction_repository.transaction_by_id(
                transaction=transaction,
                transaction_id=str(version["transaction_id"]),
            )
        if row is None or row["tenant_id"] != ctx.tenant_id:
            raise InvariantViolation("dataset serving transaction security evidence is unavailable")
        require_dataset_serving_access(
            ctx=ctx,
            policy=self.policy,
            classification=classification,
            transaction_metadata=row["metadata"],
            version_id=str(version["id"]),
        )
        return row["metadata"]

    def _storage_error_details(
        self,
        dataset_ref: str,
        dataset: DatasetRow,
        version_row: DatasetVersionRow,
        details: dict[str, object],
    ) -> dict[str, object]:
        return {
            **details,
            "dataset_ref": dataset_ref,
            "dataset_id": str(dataset["id"]),
            "version_id": str(version_row["id"]),
        }
