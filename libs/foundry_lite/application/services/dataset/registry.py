from __future__ import annotations

from typing import Any

from foundry_lite.application.ports import DatasetAlreadyExistsError
from foundry_lite.application.primitives import (
    _dataset_ref_parts,
    _new_id,
    _now,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
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
    ) -> dict[str, Any]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "dataset:write", "dataset", dataset_ref)
        namespace, name = _dataset_ref_parts(dataset_ref)
        dataset_id = _new_id("ds")
        now = _now()
        storage_uri = self.dataset_storage.dataset_uri(ctx.tenant_id, dataset_id)
        primary_key = primary_key or []
        with self.engine.begin() as conn:
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
                    created_at=now,
                    updated_at=now,
                )
            except DatasetAlreadyExistsError as exc:
                raise ConflictDetected(
                    "dataset already exists in this tenant",
                    details={"dataset_ref": dataset_ref},
                ) from exc
            self.runtime_service._audit(
                conn,
                ctx,
                event_type="dataset.created",
                resource_type="dataset",
                resource_id=dataset_id,
                action="create",
                after_ref={"dataset_ref": dataset_ref},
            )
        return self.get_dataset(dataset_ref, ctx=ctx)

    def ensure_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        primary_key: list[str] | None = None,
        storage_kind: str = "parquet_manifest",
    ) -> dict[str, Any]:
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

    def find_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> dict[str, Any] | None:
        ctx = ctx or RequestContext()
        namespace, name = _dataset_ref_parts(dataset_ref)
        return self.dataset_repository.find_active_dataset(tenant_id=ctx.tenant_id, namespace=namespace, name=name)

    def get_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> dict[str, Any]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "dataset:read", "dataset", dataset_ref)
        dataset = self.find_dataset(dataset_ref, ctx=ctx)
        if dataset is None:
            raise NotFound("dataset not found", details={"dataset_ref": dataset_ref})
        return dataset

    def list_dataset_versions(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[dict[str, Any]]:
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
    ) -> list[dict[str, Any]]:
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
    ) -> dict[str, Any]:
        ctx = ctx or RequestContext()
        dataset = self.get_dataset(dataset_ref, ctx=ctx)
        version_row = self.dataset_version_service._get_version(dataset["id"], version, ctx=ctx)
        schema_row = self.dataset_version_service._schema_for_version(dataset["id"], version_row["schema_version"])
        return {
            "dataset": dataset_ref,
            "dataset_id": dataset["id"],
            "version": dict(version_row),
            "schema": schema_row["schema_json"],
            "manifest": self.dataset_transaction_service._load_manifest(version_row["manifest_uri"]),
        }
