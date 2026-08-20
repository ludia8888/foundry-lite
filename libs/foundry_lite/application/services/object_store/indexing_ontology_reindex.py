"""Application service helpers for indexing ontology reindex workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from foundry_lite.application.ports import (
    IndexRunRow,
    IndexRunSourceRef,
    ObjectIndexRepository,
    ObjectTypeRow,
    OntologyObjectReindexResult,
    TransactionContext,
)
from foundry_lite.application.services.object_store.indexing_types import (
    ObjectIndexRebuildCounts,
    ObjectIndexRebuildPlan,
)
from foundry_lite.application.services.ontology_migration_types import (
    OBJECT_REINDEX_COMPLETE,
    completed_object_reindex,
    pending_object_reindex_operation,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


@dataclass(frozen=True)
class OntologyReindexExecution:
    """Object-index execution plan tied to one ontology migration reindex key."""

    plan: ObjectIndexRebuildPlan
    reindex_key: str
    changed_fields: tuple[str, ...]
    source_ontology_version_id: str | None


def completed_ontology_reindex_result(
    *,
    conn: TransactionContext,
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    reindex_key: str,
    object_index_repository: ObjectIndexRepository,
) -> OntologyObjectReindexResult | None:
    completed = completed_object_reindex(object_type["config"], reindex_key)
    if completed is None:
        return None
    run_id = str(completed.get("indexRunId") or "")
    row = object_index_repository.index_run_by_id(transaction=conn, tenant_id=ctx.tenant_id, run_id=run_id)
    if row is None or row["status"] != "succeeded":
        raise ValidationFailed("completed ontology reindex run is missing", details={"reindexKey": reindex_key})
    return ontology_reindex_result_from_run(object_type, row, completed)


def pending_ontology_reindex_operation(
    object_type: ObjectTypeRow,
    reindex_key: str,
) -> Mapping[str, object]:
    operation = pending_object_reindex_operation(object_type["config"], reindex_key)
    if operation is None:
        raise ValidationFailed(
            "ontology object reindex key is not pending",
            details={"objectType": object_type["api_name"], "reindexKey": reindex_key},
        )
    return operation


def ontology_reindex_execution(
    plan: ObjectIndexRebuildPlan,
    reindex_key: str,
    operation: Mapping[str, object],
) -> OntologyReindexExecution:
    return OntologyReindexExecution(
        plan=plan,
        reindex_key=reindex_key,
        changed_fields=_changed_fields(operation),
        source_ontology_version_id=_source_ontology_version_id(plan.object_type["config"]),
    )


def ontology_reindex_result(
    execution: OntologyReindexExecution,
    counts: ObjectIndexRebuildCounts,
    *,
    is_idempotent_replay: bool = False,
) -> OntologyObjectReindexResult:
    plan = execution.plan
    return {
        "index_run_id": plan.run_id,
        "object_type": plan.object_type_api_name,
        "rows_read": counts.rows_read,
        "objects_upserted": counts.objects_upserted,
        "objects_deleted": counts.objects_deleted,
        "links_upserted": counts.links_upserted,
        "ontologyReindexKey": execution.reindex_key,
        "servingContractStatus": OBJECT_REINDEX_COMPLETE,
        "isIdempotentReplay": is_idempotent_replay,
        "sourceOntologyVersionId": execution.source_ontology_version_id,
        "changedFields": list(execution.changed_fields),
    }


def ontology_reindex_result_from_run(
    object_type: ObjectTypeRow,
    row: IndexRunRow,
    completed: Mapping[str, object],
) -> OntologyObjectReindexResult:
    return {
        "index_run_id": row["id"],
        "object_type": row["object_type_api_name"],
        "rows_read": row["rows_read"],
        "objects_upserted": row["objects_upserted"],
        "objects_deleted": row["objects_deleted"],
        "links_upserted": row["links_upserted"],
        "ontologyReindexKey": str(completed["reindexKey"]),
        "servingContractStatus": str(object_type["config"].get("servingContractStatus")),
        "isIdempotentReplay": True,
        "sourceOntologyVersionId": _source_ontology_version_id(object_type["config"]),
        "changedFields": _completed_changed_fields(completed),
    }


def _changed_fields(operation: Mapping[str, object]) -> tuple[str, ...]:
    value = operation.get("changedFields")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(str(item) for item in value)


def _completed_changed_fields(completed: Mapping[str, object]) -> list[str]:
    value = completed.get("changedFields")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [str(item) for item in value]


def _source_ontology_version_id(config: Mapping[str, object]) -> str | None:
    value = config.get("sourceOntologyVersionId")
    return str(value) if isinstance(value, str) and value else None


def ontology_reindex_source_ref(
    config: Mapping[str, object],
    operation: Mapping[str, object],
    reindex_key: str,
) -> IndexRunSourceRef:
    source_ref: IndexRunSourceRef = {"ontologyReindexKey": reindex_key}
    source_ontology_version_id = _source_ontology_version_id(config)
    if source_ontology_version_id is not None:
        source_ref["sourceOntologyVersionId"] = source_ontology_version_id
    changed_fields = operation.get("changedFields")
    if isinstance(changed_fields, list):
        source_ref["changedFields"] = [str(item) for item in changed_fields]
    return source_ref
