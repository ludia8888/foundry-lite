from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import (
    DatasetVersionRow,
    IndexRunRecord,
    IndexRunSourceRef,
    ObjectTypeRow,
    TransactionContext,
)
from foundry_lite.application.ports.object_index_repository import (
    IndexRunRow,
    ObjectIndexRepository,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.object_store.indexing_protocols import (
    IndexDatasetRegistry,
    IndexDatasetVersions,
    IndexOntologyLookup,
)
from foundry_lite.application.services.object_store.indexing_types import ObjectIndexRebuildPlan
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed


def _start_index_rebuild_plan(
    *,
    conn: TransactionContext,
    ctx: RequestContext,
    object_type_api_name: str,
    dataset_registry_service: IndexDatasetRegistry,
    dataset_version_service: IndexDatasetVersions,
    ontology_service: IndexOntologyLookup,
    object_index_repository: ObjectIndexRepository,
) -> ObjectIndexRebuildPlan:
    object_type = ontology_service._active_object_type(conn, ctx, object_type_api_name)
    dataset_ref = object_type["backing"]["dataset"]
    dataset = dataset_registry_service.get_dataset(dataset_ref, ctx=ctx)
    version = dataset_version_service._latest_version_by_dataset_id(conn, dataset["id"])
    run_id = _new_id("index_run")
    _create_index_run(
        conn=conn,
        ctx=ctx,
        object_type=object_type,
        object_type_api_name=object_type_api_name,
        version=version,
        run_id=run_id,
        object_index_repository=object_index_repository,
    )
    return ObjectIndexRebuildPlan(
        run_id=run_id,
        object_type_api_name=object_type_api_name,
        object_type=object_type,
        dataset_version=version,
        source_dataset_version_id=version["id"],
    )


def _start_failed_index_replay_plan(
    *,
    conn: TransactionContext,
    ctx: RequestContext,
    index_run_id: str,
    dataset_version_service: IndexDatasetVersions,
    ontology_service: IndexOntologyLookup,
    object_index_repository: ObjectIndexRepository,
) -> ObjectIndexRebuildPlan:
    failed_run = _failed_index_run(conn, ctx, index_run_id, object_index_repository)
    version_id = _source_dataset_version_id(failed_run, index_run_id)
    version = dataset_version_service._version_by_id(conn, version_id)
    if version["tenant_id"] != ctx.tenant_id:
        raise NotFound("dataset version not found", details={"version_id": version_id})
    object_type_api_name = str(failed_run["object_type_api_name"])
    object_type = ontology_service._active_object_type(conn, ctx, object_type_api_name)
    _ensure_replay_object_type_matches(failed_run, object_type)
    run_id = _new_id("index_run")
    _create_index_run(
        conn=conn,
        ctx=ctx,
        object_type=object_type,
        object_type_api_name=object_type_api_name,
        version=version,
        run_id=run_id,
        object_index_repository=object_index_repository,
        trigger_type="failed_run_replay",
        source_ref={"dataset_version_id": version_id, "replay_of_run_id": index_run_id},
    )
    return ObjectIndexRebuildPlan(run_id, object_type_api_name, object_type, version, version_id)


def _failed_index_run(
    conn: TransactionContext,
    ctx: RequestContext,
    index_run_id: str,
    object_index_repository: ObjectIndexRepository,
) -> IndexRunRow:
    row = object_index_repository.index_run_by_id(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        run_id=index_run_id,
    )
    if row is None:
        raise NotFound("index run not found", details={"index_run_id": index_run_id})
    if row["status"] != "failed":
        raise ValidationFailed("index run is not failed", details={"index_run_id": index_run_id})
    return row


def _source_dataset_version_id(row: IndexRunRow, index_run_id: str) -> str:
    source_ref: Mapping[str, object] = row["source_ref"]
    version_id = source_ref.get("dataset_version_id")
    if not isinstance(version_id, str) or not version_id:
        raise ValidationFailed("index run source is not replayable", details={"index_run_id": index_run_id})
    return version_id


def _ensure_replay_object_type_matches(row: IndexRunRow, object_type: ObjectTypeRow) -> None:
    if row["object_type_id"] != object_type["id"]:
        raise ValidationFailed(
            "index run object type is no longer active",
            details={"index_run_id": row["id"], "object_type": row["object_type_api_name"]},
        )


def _create_index_run(
    *,
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    object_type_api_name: str,
    version: DatasetVersionRow,
    run_id: str,
    object_index_repository: ObjectIndexRepository,
    trigger_type: str = "reindex",
    source_ref: IndexRunSourceRef | None = None,
) -> None:
    now = _now()
    source = source_ref or {"dataset_version_id": version["id"]}
    object_index_repository.create_index_run(
        transaction=conn,
        record=IndexRunRecord(
            run_id=run_id,
            tenant_id=ctx.tenant_id,
            object_type_id=object_type["id"],
            object_type_api_name=object_type_api_name,
            trigger_type=trigger_type,
            source_ref=source,
            status="running",
            cursor={},
            rows_read=0,
            objects_upserted=0,
            objects_deleted=0,
            links_upserted=0,
            error=None,
            started_at=now,
            completed_at=None,
            created_at=now,
        ),
    )
