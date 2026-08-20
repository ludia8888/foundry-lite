"""Application service helpers for indexing runs workflows."""

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
from foundry_lite.application.services.object_store.indexing_multi_source import (
    multi_source_version_id,
    multi_source_version_ids_by_name,
    resolve_multi_source_plan,
)
from foundry_lite.application.services.object_store.indexing_protocols import (
    IndexDatasetRegistry,
    IndexDatasetVersions,
    IndexOntologyLookup,
)
from foundry_lite.application.services.object_store.indexing_types import (
    ObjectIndexLinkSource,
    ObjectIndexMultiSourcePlan,
    ObjectIndexRebuildPlan,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.domain.ontology.datasources import primary_dataset_ref


def _resolve_plan_inputs(
    *,
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    dataset_registry_service: IndexDatasetRegistry,
    dataset_version_service: IndexDatasetVersions,
    ontology_service: IndexOntologyLookup,
    object_index_repository: ObjectIndexRepository,
) -> tuple[DatasetVersionRow, str, ObjectIndexMultiSourcePlan | None, dict[str, ObjectIndexLinkSource]]:
    version, source_version_id, multi_source = _resolve_plan_source(
        conn,
        ctx,
        object_type,
        dataset_registry_service=dataset_registry_service,
        dataset_version_service=dataset_version_service,
        ontology_service=ontology_service,
    )
    link_sources = _resolve_link_sources(
        conn,
        ctx,
        object_type,
        dataset_registry_service=dataset_registry_service,
        dataset_version_service=dataset_version_service,
        ontology_service=ontology_service,
        object_index_repository=object_index_repository,
    )
    return version, source_version_id, multi_source, link_sources


def _resolve_link_sources(
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    *,
    dataset_registry_service: IndexDatasetRegistry,
    dataset_version_service: IndexDatasetVersions,
    ontology_service: IndexOntologyLookup,
    object_index_repository: ObjectIndexRepository,
) -> dict[str, ObjectIndexLinkSource]:
    """Pin the join datasource version behind every outbound many-to-many link.

    Direct links read the object's own rows, so the object plan's source version already
    describes them. A many-to-many link reads a separate join dataset that advances on its
    own schedule, so the version it will read is fixed here — before the run row exists —
    rather than at read time, where a concurrent upload would silently change the answer.
    """
    active = ontology_service._active_ontology_version(conn, ctx)
    links = object_index_repository.link_types_for_object_type(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        ontology_version_id=active["id"],
        from_object_type_id=object_type["id"],
    )
    sources: dict[str, ObjectIndexLinkSource] = {}
    for link in links:
        if link["cardinality"] != "many_to_many":
            continue
        dataset_ref = link["backing"].get("dataset")
        if not isinstance(dataset_ref, str) or not dataset_ref:
            raise ValidationFailed(
                "many-to-many link backing dataset missing",
                details={"linkType": link["api_name"]},
            )
        dataset = dataset_registry_service.get_dataset(dataset_ref, ctx=ctx)
        version = dataset_version_service._latest_version_by_dataset_id(conn, dataset["id"])
        sources[str(link["api_name"])] = ObjectIndexLinkSource(dataset_ref, version)
    return sources


def _replay_link_sources(
    conn: TransactionContext,
    failed_run: IndexRunRow,
    *,
    dataset_version_service: IndexDatasetVersions,
) -> dict[str, ObjectIndexLinkSource]:
    """Restore the exact join versions the failed run pinned, never today's latest.

    Replaying against a newer join snapshot would produce a different link set under the
    same run id, which is the one thing the pin exists to prevent.
    """
    source_ref = failed_run["source_ref"] if isinstance(failed_run["source_ref"], Mapping) else {}
    version_ids = source_ref.get("linkSourceDatasetVersionIds")
    dataset_refs = source_ref.get("linkSourceDatasetRefs")
    if not isinstance(version_ids, Mapping):
        return {}
    refs = dataset_refs if isinstance(dataset_refs, Mapping) else {}
    return {
        str(api_name): ObjectIndexLinkSource(
            str(refs.get(api_name, "")),
            dataset_version_service._version_by_id(conn, str(version_id)),
        )
        for api_name, version_id in version_ids.items()
    }


def _link_source_ref(link_sources: Mapping[str, ObjectIndexLinkSource]) -> IndexRunSourceRef:
    """Operator evidence: which join dataset and version each M:N link consumed."""
    if not link_sources:
        return {}
    ref: IndexRunSourceRef = {
        "linkSourceDatasetVersionIds": {
            api_name: str(source.dataset_version["id"]) for api_name, source in link_sources.items()
        },
        "linkSourceDatasetRefs": {api_name: source.dataset_ref for api_name, source in link_sources.items()},
    }
    return ref


def _resolve_plan_source(
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    *,
    dataset_registry_service: IndexDatasetRegistry,
    dataset_version_service: IndexDatasetVersions,
    ontology_service: IndexOntologyLookup,
) -> tuple[DatasetVersionRow, str, ObjectIndexMultiSourcePlan | None]:
    """Resolve the latest source snapshot: one version, or one per datasource segment."""
    properties = ontology_service._properties_for_object_type(conn, object_type["id"])
    multi_source = resolve_multi_source_plan(
        conn,
        ctx,
        object_type,
        properties,
        dataset_registry_service=dataset_registry_service,
        dataset_version_service=dataset_version_service,
    )
    if multi_source is not None:
        return multi_source.segments[0].dataset_version, multi_source_version_id(multi_source), multi_source
    dataset = dataset_registry_service.get_dataset(primary_dataset_ref(object_type["backing"]), ctx=ctx)
    version = dataset_version_service._latest_version_by_dataset_id(conn, dataset["id"])
    return version, version["id"], None


def _rebuild_plan_with_run(
    *,
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    object_type_api_name: str,
    version: DatasetVersionRow,
    source_version_id: str,
    multi_source: ObjectIndexMultiSourcePlan | None,
    object_index_repository: ObjectIndexRepository,
    mode: str,
    link_sources: Mapping[str, ObjectIndexLinkSource],
) -> ObjectIndexRebuildPlan:
    """Record a plain (full/shadow) rebuild run and return its plan."""
    run_id, index_version, active_index_version = _create_index_run_with_versions(
        conn=conn,
        ctx=ctx,
        object_type=object_type,
        object_type_api_name=object_type_api_name,
        source_version_id=source_version_id,
        mode=mode,
        object_index_repository=object_index_repository,
        trigger_type=_rebuild_trigger_type(mode),
        source_ref=_link_source_ref(link_sources) or None,
        multi_source=multi_source,
    )
    return _rebuild_plan(
        run_id,
        object_type,
        object_type_api_name,
        version,
        source_version_id,
        mode,
        index_version,
        active_index_version,
        multi_source,
        link_sources,
    )


def _rebuild_plan(
    run_id: str,
    object_type: ObjectTypeRow,
    object_type_api_name: str,
    version: DatasetVersionRow,
    source_version_id: str,
    mode: str,
    index_version: str,
    active_index_version: str,
    multi_source: ObjectIndexMultiSourcePlan | None,
    link_sources: Mapping[str, ObjectIndexLinkSource],
) -> ObjectIndexRebuildPlan:
    return ObjectIndexRebuildPlan(
        run_id,
        object_type_api_name,
        object_type,
        version,
        source_version_id,
        mode,
        index_version,
        active_index_version,
        trigger_type=_rebuild_trigger_type(mode),
        multi_source=multi_source,
        link_sources=link_sources,
    )


def _start_full_plan_with_run(
    *,
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    object_type_api_name: str,
    version: DatasetVersionRow,
    source_version_id: str,
    multi_source: ObjectIndexMultiSourcePlan | None,
    object_index_repository: ObjectIndexRepository,
    trigger_type: str,
    source_ref: IndexRunSourceRef,
    link_sources: Mapping[str, ObjectIndexLinkSource],
) -> ObjectIndexRebuildPlan:
    """Record a full-mode run for ontology-migration reindexes and failed-run replays."""
    run_id, _index_version, active_index_version = _create_index_run_with_versions(
        conn=conn,
        ctx=ctx,
        object_type=object_type,
        object_type_api_name=object_type_api_name,
        source_version_id=source_version_id,
        mode="full",
        object_index_repository=object_index_repository,
        trigger_type=trigger_type,
        source_ref={**source_ref, **_link_source_ref(link_sources)},
        multi_source=multi_source,
    )
    return _full_rebuild_plan(
        run_id,
        object_type_api_name,
        object_type,
        version,
        source_version_id,
        active_index_version,
        trigger_type=trigger_type,
        multi_source=multi_source,
        link_sources=link_sources,
    )


def _create_index_run_with_versions(
    *,
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    object_type_api_name: str,
    source_version_id: str,
    mode: str,
    object_index_repository: ObjectIndexRepository,
    trigger_type: str,
    source_ref: IndexRunSourceRef | None,
    multi_source: ObjectIndexMultiSourcePlan | None,
) -> tuple[str, str, str]:
    run_id = _new_id("index_run")
    active_index_version = _active_index_version(conn, ctx, object_type, object_index_repository)
    index_version = run_id if mode == "shadow" else active_index_version
    _create_index_run(
        conn=conn,
        ctx=ctx,
        object_type=object_type,
        object_type_api_name=object_type_api_name,
        source_version_id=source_version_id,
        run_id=run_id,
        mode=mode,
        index_version=index_version,
        object_index_repository=object_index_repository,
        trigger_type=trigger_type,
        source_ref=source_ref,
        multi_source=multi_source,
    )
    return run_id, index_version, active_index_version


def _full_rebuild_plan(
    run_id: str,
    object_type_api_name: str,
    object_type: ObjectTypeRow,
    version: DatasetVersionRow,
    source_version_id: str,
    active_index_version: str,
    *,
    trigger_type: str,
    multi_source: ObjectIndexMultiSourcePlan | None,
    link_sources: Mapping[str, ObjectIndexLinkSource],
) -> ObjectIndexRebuildPlan:
    return ObjectIndexRebuildPlan(
        run_id,
        object_type_api_name,
        object_type,
        version,
        source_version_id,
        "full",
        active_index_version,
        active_index_version,
        trigger_type=trigger_type,
        multi_source=multi_source,
        link_sources=link_sources,
    )


def _active_index_version(
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    object_index_repository: ObjectIndexRepository,
) -> str:
    return object_index_repository.active_index_version(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        object_type_id=object_type["id"],
    )


def _create_index_run(
    *,
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    object_type_api_name: str,
    source_version_id: str,
    run_id: str,
    mode: str,
    index_version: str,
    object_index_repository: ObjectIndexRepository,
    trigger_type: str = "reindex",
    source_ref: IndexRunSourceRef | None = None,
    multi_source: ObjectIndexMultiSourcePlan | None = None,
) -> None:
    now = _now()
    source = _index_run_source_ref(source_version_id, mode, index_version, source_ref, multi_source)
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


def _index_run_source_ref(
    source_version_id: str,
    mode: str,
    index_version: str,
    source_ref: IndexRunSourceRef | None,
    multi_source: ObjectIndexMultiSourcePlan | None,
) -> IndexRunSourceRef:
    source: IndexRunSourceRef = {"dataset_version_id": source_version_id}
    if multi_source is not None:
        # Operator evidence: which concrete version each datasource contributed.
        source["segmentDatasetVersionIds"] = multi_source_version_ids_by_name(multi_source)
    if source_ref is not None:
        source.update(source_ref)
    source["mode"] = mode
    source["index_version"] = index_version
    return source


def _rebuild_trigger_type(mode: str) -> str:
    return "shadow_reindex" if mode == "shadow" else "reindex"
