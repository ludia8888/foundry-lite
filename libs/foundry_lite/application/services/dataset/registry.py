from __future__ import annotations

from foundry_lite.application.ports import (
    DatasetAlreadyExistsError,
    DatasetInspectionPayload,
    DatasetManifest,
    DatasetRow,
    DatasetVersionRow,
    TabularRow,
    TransactionContext,
)
from foundry_lite.application.primitives import (
    _dataset_ref_parts,
    _new_id,
    _now,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.protocols import (
    DatasetRuntimeBoundary,
    DatasetTransactionManager,
    DatasetVersionLookup,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    InvariantViolation,
    NotFound,
)


class DatasetRegistryService(CoreService):
    required_dependencies = (
        "engine",
        "policy",
        "compute_adapter",
        "dataset_repository",
        "dataset_version_repository",
        "dataset_storage",
    )
    required_collaborators = (
        "dataset_transaction_service",
        "dataset_version_service",
        "runtime_service",
    )
    dataset_transaction_service: DatasetTransactionManager
    dataset_version_service: DatasetVersionLookup
    runtime_service: DatasetRuntimeBoundary

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
    ) -> DatasetRow:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "dataset:write", "dataset", dataset_ref)
        namespace, name = _dataset_ref_parts(dataset_ref)
        dataset_id = _new_id("ds")
        now = _now()
        storage_uri = self.dataset_storage.dataset_uri(ctx.tenant_id, dataset_id)
        primary_key = primary_key or []
        with self.engine.begin() as conn:
            self._insert_dataset_record(
                conn,
                ctx,
                dataset_ref,
                dataset_id=dataset_id,
                namespace=namespace,
                name=name,
                description=description,
                storage_kind=storage_kind,
                storage_uri=storage_uri,
                owner_team=owner_team,
                classification=classification,
                primary_key=primary_key,
                created_at=now,
            )
            self._audit_dataset_created(conn, ctx, dataset_id, dataset_ref)
        return self.get_dataset(dataset_ref, ctx=ctx)

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
        *,
        dataset_id: str,
        namespace: str,
        name: str,
        description: str | None,
        storage_kind: str,
        storage_uri: str,
        owner_team: str | None,
        classification: str | None,
        primary_key: list[str],
        created_at: str,
    ) -> None:
        try:
            self.dataset_repository.create_dataset(
                transaction=conn,
                dataset_id=dataset_id,
                tenant_id=ctx.tenant_id,
                namespace=namespace,
                name=name,
                description=description,
                storage_kind=storage_kind,
                storage_uri=storage_uri,
                owner_team=owner_team,
                classification=classification,
                primary_key=primary_key,
                created_at=created_at,
                updated_at=created_at,
            )
        except DatasetAlreadyExistsError as exc:
            raise ConflictDetected(
                "dataset already exists in this tenant",
                details={"dataset_ref": dataset_ref},
            ) from exc

    def ensure_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        primary_key: list[str] | None = None,
        storage_kind: str = "parquet_manifest",
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
        )

    def find_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> DatasetRow | None:
        ctx = ctx or RequestContext()
        namespace, name = _dataset_ref_parts(dataset_ref)
        return self.dataset_repository.find_active_dataset(tenant_id=ctx.tenant_id, namespace=namespace, name=name)

    def get_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> DatasetRow:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "dataset:read")
        dataset = self.find_dataset(dataset_ref, ctx=ctx)
        if dataset is None:
            raise NotFound("dataset not found", details={"dataset_ref": dataset_ref})
        return dataset

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
    ) -> list[TabularRow]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "dataset:read")
        dataset = self.get_dataset(dataset_ref, ctx=ctx)
        version_row = self.dataset_version_service._get_version(dataset["id"], version, ctx=ctx)
        parquet_path = self.dataset_transaction_service._version_file_path(version_row)
        return self.compute_adapter.preview_parquet(parquet_path, limit=int(limit))

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
        schema_row = self.dataset_version_service._schema_for_version(dataset["id"], version_row["schema_version"])
        return {
            "dataset": dataset_ref,
            "dataset_id": dataset["id"],
            "version": version_row,
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
            return self.dataset_transaction_service._load_manifest(version_row["manifest_uri"])
        except InvariantViolation as exc:
            details = self._storage_error_details(dataset_ref, dataset, version_row, exc.details)
            raise InvariantViolation(exc.message, details=details) from exc

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
