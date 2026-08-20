"""Startup planning for normal, ontology-triggered, and replayed index runs."""

from __future__ import annotations

from foundry_lite.application.ports import DatasetVersionRow, ObjectTypeRow, TransactionContext
from foundry_lite.application.ports.object_index_repository import IndexRunRow, ObjectIndexRepository
from foundry_lite.application.services.object_store.indexing_multi_source import replay_multi_source_plan
from foundry_lite.application.services.object_store.indexing_ontology_reindex import (
    ontology_reindex_source_ref,
    pending_ontology_reindex_operation,
)
from foundry_lite.application.services.object_store.indexing_protocols import (
    IndexDatasetRegistry,
    IndexDatasetVersions,
    IndexOntologyLookup,
)
from foundry_lite.application.services.object_store.indexing_runs import (
    _rebuild_plan_with_run,
    _replay_link_sources,
    _resolve_plan_inputs,
    _start_full_plan_with_run,
)
from foundry_lite.application.services.object_store.indexing_types import (
    ObjectIndexMultiSourcePlan,
    ObjectIndexRebuildPlan,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed
from foundry_lite.domain.ontology.datasources import is_multi_datasource_backing


def start_failed_index_replay_plan(
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
    object_type_api_name = str(failed_run["object_type_api_name"])
    object_type = ontology_service._active_object_type(conn, ctx, object_type_api_name)
    _ensure_replay_object_type_matches(failed_run, object_type)
    version, multi_source = _replay_plan_source(
        conn,
        ctx,
        object_type,
        version_id,
        dataset_version_service=dataset_version_service,
        ontology_service=ontology_service,
    )
    return _start_full_plan_with_run(
        conn=conn,
        ctx=ctx,
        object_type=object_type,
        object_type_api_name=object_type_api_name,
        version=version,
        source_version_id=version_id,
        multi_source=multi_source,
        object_index_repository=object_index_repository,
        trigger_type="failed_run_replay",
        source_ref={"dataset_version_id": version_id, "replay_of_run_id": index_run_id},
        link_sources=_replay_link_sources(conn, failed_run, dataset_version_service=dataset_version_service),
    )


def start_index_rebuild_plan(
    *,
    conn: TransactionContext,
    ctx: RequestContext,
    object_type_api_name: str,
    dataset_registry_service: IndexDatasetRegistry,
    dataset_version_service: IndexDatasetVersions,
    ontology_service: IndexOntologyLookup,
    object_index_repository: ObjectIndexRepository,
    mode: str = "full",
) -> ObjectIndexRebuildPlan:
    object_type = ontology_service._active_object_type(conn, ctx, object_type_api_name)
    version, source_version_id, multi_source, link_sources = _resolve_plan_inputs(
        conn=conn,
        ctx=ctx,
        object_type=object_type,
        dataset_registry_service=dataset_registry_service,
        dataset_version_service=dataset_version_service,
        ontology_service=ontology_service,
        object_index_repository=object_index_repository,
    )
    return _rebuild_plan_with_run(
        conn=conn,
        ctx=ctx,
        object_type=object_type,
        object_type_api_name=object_type_api_name,
        version=version,
        source_version_id=source_version_id,
        multi_source=multi_source,
        object_index_repository=object_index_repository,
        mode=mode,
        link_sources=link_sources,
    )


def start_ontology_reindex_plan(
    *,
    conn: TransactionContext,
    ctx: RequestContext,
    object_type_api_name: str,
    reindex_key: str,
    dataset_registry_service: IndexDatasetRegistry,
    dataset_version_service: IndexDatasetVersions,
    ontology_service: IndexOntologyLookup,
    object_index_repository: ObjectIndexRepository,
) -> ObjectIndexRebuildPlan:
    object_type = ontology_service._active_object_type(conn, ctx, object_type_api_name)
    operation = pending_ontology_reindex_operation(object_type, reindex_key)
    version, source_version_id, multi_source, link_sources = _resolve_plan_inputs(
        conn=conn,
        ctx=ctx,
        object_type=object_type,
        dataset_registry_service=dataset_registry_service,
        dataset_version_service=dataset_version_service,
        ontology_service=ontology_service,
        object_index_repository=object_index_repository,
    )
    return _start_full_plan_with_run(
        conn=conn,
        ctx=ctx,
        object_type=object_type,
        object_type_api_name=object_type_api_name,
        version=version,
        source_version_id=source_version_id,
        multi_source=multi_source,
        object_index_repository=object_index_repository,
        trigger_type="ontology_migration_reindex",
        source_ref=ontology_reindex_source_ref(object_type["config"], operation, reindex_key),
        link_sources=link_sources,
    )


def _replay_plan_source(
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    version_id: str,
    *,
    dataset_version_service: IndexDatasetVersions,
    ontology_service: IndexOntologyLookup,
) -> tuple[DatasetVersionRow, ObjectIndexMultiSourcePlan | None]:
    """Rebind the recorded single or multi-datasource source version."""
    if is_multi_datasource_backing(object_type["backing"]):
        properties = ontology_service._properties_for_object_type(conn, object_type["id"])
        multi_source = replay_multi_source_plan(
            conn,
            ctx,
            object_type,
            properties,
            version_id,
            dataset_version_service=dataset_version_service,
        )
        return multi_source.segments[0].dataset_version, multi_source
    version = dataset_version_service._version_by_id(conn, version_id)
    if version["tenant_id"] != ctx.tenant_id:
        raise NotFound("dataset version not found", details={"version_id": version_id})
    return version, None


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
    source_ref = row["source_ref"]
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
