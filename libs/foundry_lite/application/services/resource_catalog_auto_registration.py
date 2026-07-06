"""Shared resource catalog registration for existing work-product mutations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.ports import SourceConnectionRow, TransactionContext
from foundry_lite.application.ports.resource_catalog_repository import (
    ProjectRow,
    ResourceCatalogRepository,
    ResourceRow,
)
from foundry_lite.application.services.resource_catalog_records import (
    owner_grant_record,
    project_record,
    resource_record,
)
from foundry_lite.domain.context import RequestContext


class ResourceRegistrationRuntime(Protocol):
    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str | None,
        action: str,
        decision: str = "allow",
        policy_decision: Mapping[str, object] | None = None,
        before_ref: Mapping[str, object] | None = None,
        after_ref: Mapping[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> None: ...

    def _outbox(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> str | None: ...


def upsert_work_product_resource(
    *,
    conn: TransactionContext,
    ctx: RequestContext,
    repository: ResourceCatalogRepository,
    runtime_service: ResourceRegistrationRuntime,
    resource_type: str,
    display_name: str,
    source_surface: str,
    source_ref: str,
    operations_path: str,
    metadata: Mapping[str, object],
    now: str,
    idempotency_key: str,
    evidence: Mapping[str, object],
) -> ResourceRow:
    project_id = _resource_project_id(conn, ctx, repository, now)
    row = _upsert_resource_row(
        conn=conn,
        ctx=ctx,
        repository=repository,
        resource_type=resource_type,
        display_name=display_name,
        project_id=project_id,
        source_surface=source_surface,
        source_ref=source_ref,
        operations_path=operations_path,
        metadata=metadata,
        now=now,
    )
    payload = {"resourceRid": row["rid"], **dict(evidence)}
    _record_resource_registration(
        conn=conn,
        ctx=ctx,
        runtime_service=runtime_service,
        resource_rid=row["rid"],
        payload=payload,
        idempotency_key=idempotency_key,
    )
    return row


def upsert_source_resource(
    conn: TransactionContext,
    ctx: RequestContext,
    repository: ResourceCatalogRepository,
    runtime_service: ResourceRegistrationRuntime,
    row: SourceConnectionRow,
) -> ResourceRow:
    return upsert_work_product_resource(
        conn=conn,
        ctx=ctx,
        repository=repository,
        runtime_service=runtime_service,
        resource_type="source",
        display_name=row["display_name"],
        source_surface="source",
        source_ref=row["source_name"],
        operations_path=f"/sources/{row['source_name']}",
        metadata={"sourceId": row["id"], "kind": row["kind"], "status": row["status"]},
        now=row["created_at"],
        idempotency_key=f"source-resource:{row['source_name']}",
        evidence={"sourceName": row["source_name"]},
    )


def upsert_pipeline_resource(
    conn: TransactionContext,
    ctx: RequestContext,
    repository: ResourceCatalogRepository,
    runtime_service: ResourceRegistrationRuntime,
    row: Mapping[str, object],
) -> ResourceRow:
    pipeline_id = str(row["pipeline_id"])
    branch_id = str(row["id"])
    return upsert_work_product_resource(
        conn=conn,
        ctx=ctx,
        repository=repository,
        runtime_service=runtime_service,
        resource_type="pipeline",
        display_name=f"{pipeline_id} / {row['name']}",
        source_surface="pipeline_branch",
        source_ref=branch_id,
        operations_path=f"/pipelines/{pipeline_id}/branches/{branch_id}",
        metadata={"pipelineId": pipeline_id, "branchId": branch_id, "status": row["status"]},
        now=str(row["created_at"]),
        idempotency_key=f"pipeline-resource:{branch_id}",
        evidence={"branchId": branch_id},
    )


def upsert_ontology_resource(
    *,
    conn: TransactionContext,
    ctx: RequestContext,
    repository: ResourceCatalogRepository,
    runtime_service: ResourceRegistrationRuntime,
    ontology_version_id: str,
    version_number: int,
    now: str,
) -> ResourceRow:
    return upsert_work_product_resource(
        conn=conn,
        ctx=ctx,
        repository=repository,
        runtime_service=runtime_service,
        resource_type="ontology",
        display_name=f"Ontology v{version_number}",
        source_surface="ontology_version",
        source_ref=ontology_version_id,
        operations_path=f"/ontology/versions/{ontology_version_id}",
        metadata={"versionNumber": version_number, "status": "active"},
        now=now,
        idempotency_key=f"ontology-resource:{ontology_version_id}",
        evidence={"ontologyVersionId": ontology_version_id},
    )


def _upsert_resource_row(
    *,
    conn: TransactionContext,
    ctx: RequestContext,
    repository: ResourceCatalogRepository,
    resource_type: str,
    display_name: str,
    project_id: str,
    source_surface: str,
    source_ref: str,
    operations_path: str,
    metadata: Mapping[str, object],
    now: str,
) -> ResourceRow:
    return repository.upsert_resource(
        transaction=conn,
        record=resource_record(
            ctx,
            resource_type=resource_type,
            display_name=display_name,
            project_id=project_id,
            folder_id=None,
            source_surface=source_surface,
            source_ref=source_ref,
            operations_path=operations_path,
            metadata=metadata,
            now=now,
        ),
    )


def _record_resource_registration(
    *,
    conn: TransactionContext,
    ctx: RequestContext,
    runtime_service: ResourceRegistrationRuntime,
    resource_rid: str,
    payload: Mapping[str, object],
    idempotency_key: str,
) -> None:
    runtime_service._audit(
        conn,
        ctx,
        event_type="resource.registered",
        resource_type="resource",
        resource_id=resource_rid,
        action="register",
        after_ref=payload,
    )
    runtime_service._outbox(
        conn,
        ctx,
        "resource.registered",
        "resource",
        resource_rid,
        payload,
        idempotency_key=idempotency_key,
        correlation_id=ctx.request_id,
    )


def _resource_project_id(
    conn: TransactionContext,
    ctx: RequestContext,
    repository: ResourceCatalogRepository,
    now: str,
) -> str:
    personal = _personal_project(conn, ctx, repository)
    if personal is not None:
        return personal["id"]
    project = repository.insert_project(
        transaction=conn,
        record=project_record(
            ctx,
            display_name=f"{ctx.actor_user_id} Home",
            description=None,
            metadata={"kind": "personal"},
            now=now,
        ),
    )
    repository.upsert_project_grant(
        transaction=conn,
        record=owner_grant_record(ctx, project["id"], now=now),
    )
    return project["id"]


def _personal_project(
    conn: TransactionContext,
    ctx: RequestContext,
    repository: ResourceCatalogRepository,
) -> ProjectRow | None:
    for row in repository.list_projects(transaction=conn, tenant_id=ctx.tenant_id):
        if row["created_by_user_id"] == ctx.actor_user_id and dict(row["metadata"]).get("kind") == "personal":
            return row
    return None
