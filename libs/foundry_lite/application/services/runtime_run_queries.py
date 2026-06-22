from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from foundry_lite.application.ports import (
    LineageEdgeRow,
    RuntimeRow,
    RuntimeRunInvestigation,
    RuntimeRunLink,
    RuntimeRunSnapshot,
    RuntimeRunType,
)
from foundry_lite.application.services.runtime_late_data_impact import (
    downstream_impact_graph as _downstream_impact_graph,
)
from foundry_lite.application.services.runtime_late_data_impact import (
    materialization_late_data_badge,
)
from foundry_lite.application.services.runtime_late_data_impact import (
    object_late_data_badge as _object_late_data_badge,
)
from foundry_lite.domain.errors import ValidationFailed

downstream_impact_graph = _downstream_impact_graph
object_late_data_badge = _object_late_data_badge

RUN_GROUPS: Mapping[RuntimeRunType, str] = {
    "sync": "syncRuns",
    "transform": "transformRuns",
    "index": "indexRuns",
    "action": "actionRuns",
    "action_writeback": "actionWritebacks",
    "materialization": "materializationRuns",
    "outbox": "outboxEvents",
    "dead_letter": "deadLetterEvents",
    "workflow": "workflowRuns",
    "audit": "auditEvents",
}


def optional_run_type(value: str | None) -> RuntimeRunType | None:
    if value is None or value == "":
        return None
    return required_run_type(value)


def required_run_type(value: str) -> RuntimeRunType:
    normalized = value.replace("-", "_")
    if normalized in RUN_GROUPS:
        return cast(RuntimeRunType, normalized)
    raise ValidationFailed("unsupported operations run type", details={"run_type": value})


def filtered_snapshot(
    snapshot: RuntimeRunSnapshot,
    *,
    run_type: RuntimeRunType | None,
    status: str | None,
    since: str | None,
    until: str | None,
) -> RuntimeRunSnapshot:
    return {
        "syncRuns": _filtered_rows(snapshot["syncRuns"], run_type, "sync", status, since, until),
        "transformRuns": _filtered_rows(snapshot["transformRuns"], run_type, "transform", status, since, until),
        "indexRuns": _filtered_rows(snapshot["indexRuns"], run_type, "index", status, since, until),
        "actionRuns": _filtered_rows(snapshot["actionRuns"], run_type, "action", status, since, until),
        "actionWritebacks": _filtered_rows(
            snapshot["actionWritebacks"], run_type, "action_writeback", status, since, until
        ),
        "materializationRuns": _filtered_rows(
            snapshot["materializationRuns"], run_type, "materialization", status, since, until
        ),
        "outboxEvents": _filtered_rows(snapshot["outboxEvents"], run_type, "outbox", status, since, until),
        "deadLetterEvents": _filtered_rows(snapshot["deadLetterEvents"], run_type, "dead_letter", status, since, until),
        "workflowRuns": _filtered_rows(snapshot["workflowRuns"], run_type, "workflow", status, since, until),
        "auditEvents": _filtered_rows(snapshot["auditEvents"], run_type, "audit", status, since, until),
        "objectEdits": _filtered_support_rows(snapshot["objectEdits"], run_type, status, since, until),
    }


def row_status(row: RuntimeRow, run_type: RuntimeRunType | None = None) -> str:
    value = row.get("status") or row.get("decision")
    if isinstance(value, str) and value:
        return value
    if run_type == "dead_letter":
        return "dead_lettered"
    return "unknown"


def correlation_id(row: RuntimeRow) -> str | None:
    for key in ("correlation_id", "id", "action_run_id", "run_id", "transaction_id"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def late_data_detail(dataset_transaction: RuntimeRow | None) -> dict[str, object] | None:
    if dataset_transaction is None:
        return None
    metadata = dataset_transaction.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    summary = metadata.get("lateDataSummary")
    watermark = metadata.get("lateDataWatermark")
    reprocessing_plan = metadata.get("lateDataReprocessingPlan")
    if summary is None and watermark is None and reprocessing_plan is None:
        return None
    return {"summary": summary, "watermark": watermark, "reprocessingPlan": reprocessing_plan}


def materialization_detail(row: RuntimeRow, dataset_transaction: RuntimeRow | None) -> dict[str, object] | None:
    metadata = _transaction_metadata(dataset_transaction)
    detail = metadata.get("materializationDetail") if metadata is not None else None
    if isinstance(detail, Mapping):
        return _materialization_detail_with_badge(detail)
    watermark = row.get("object_store_watermark") or row.get("source_cursor")
    if not isinstance(watermark, Mapping):
        return None
    return _materialization_detail_with_badge(
        {
            "apiName": row.get("api_name"),
            "watermark": dict(watermark),
            "reopen": {"isReopened": False},
        }
    )


def _materialization_detail_with_badge(detail: Mapping[str, object]) -> dict[str, object]:
    payload = dict(detail)
    badge = materialization_late_data_badge(payload)
    if badge is not None:
        payload["lateDataBadge"] = badge
    return payload


def _transaction_metadata(dataset_transaction: RuntimeRow | None) -> Mapping[str, object] | None:
    if dataset_transaction is None:
        return None
    metadata = dataset_transaction.get("metadata")
    return metadata if isinstance(metadata, Mapping) else None


def error_message(error: object) -> str | None:
    if isinstance(error, Mapping):
        message = error.get("message")
        return str(message) if message else str(error)
    if error is None:
        return None
    return str(error)


def row_references(row: RuntimeRow) -> dict[str, object]:
    keys = (
        "transaction_id",
        "committed_version_id",
        "output_version_id",
        "target_dataset_version_id",
        "input_versions",
        "target_object_id",
        "aggregate_id",
        "source_event_id",
    )
    return {key: row[key] for key in keys if key in row and row[key] is not None}


def resource_ids_for_lineage(row: RuntimeRow) -> set[str]:
    resources: set[str] = set()
    for key in ("committed_version_id", "output_version_id", "target_dataset_version_id"):
        value = row.get(key)
        if isinstance(value, str) and value:
            resources.add(value)
    input_versions = row.get("input_versions")
    if isinstance(input_versions, Mapping):
        resources.update(str(value) for value in input_versions.values() if isinstance(value, str) and value)
    return resources


def run_investigation(
    row: RuntimeRow,
    run_type: RuntimeRunType,
    *,
    related_outbox_count: int,
    related_audit_count: int,
    related_object_edit_count: int,
    related_action_writeback_count: int,
    lineage_edge_count: int,
) -> RuntimeRunInvestigation:
    status = row_status(row, run_type)
    message = error_message(row.get("error"))
    refs = row_references(row)
    return {
        "summary": _investigation_summary(run_type, str(row["id"]), status, message),
        "status": status,
        "errorMessage": message,
        "correlationId": correlation_id(row),
        "references": refs,
        "relatedCounts": {
            "outboxEvents": related_outbox_count,
            "auditEvents": related_audit_count,
            "objectEdits": related_object_edit_count,
            "actionWritebacks": related_action_writeback_count,
            "lineageEdges": lineage_edge_count,
        },
        "suggestedActions": _suggested_actions(run_type, str(row["id"]), status),
    }


def source_run_chain(
    snapshot: RuntimeRunSnapshot,
    *,
    source_dataset_version_id: str,
    object_type_api_name: str,
    lineage_rows: list[LineageEdgeRow],
) -> list[RuntimeRunLink]:
    links = _cdc_index_run_links(snapshot, source_dataset_version_id, object_type_api_name)
    links.extend(_index_run_links(snapshot, source_dataset_version_id, object_type_api_name))
    resource_ids = relation_resource_ids(source_dataset_version_id, lineage_rows)
    for resource_id in resource_ids:
        relation = "source_producer" if resource_id == source_dataset_version_id else "upstream_producer"
        links.extend(_producer_run_links(snapshot, resource_id, relation))
    links.extend(_lineage_run_links(snapshot, source_dataset_version_id, lineage_rows))
    return _dedupe_run_links(links)


def _filtered_rows(
    rows: list[RuntimeRow],
    selected_type: RuntimeRunType | None,
    current_type: RuntimeRunType,
    status: str | None,
    since: str | None,
    until: str | None,
) -> list[RuntimeRow]:
    if selected_type is not None and selected_type != current_type:
        return []
    return [row for row in rows if _matches_status(row, current_type, status) and _matches_date(row, since, until)]


def _filtered_support_rows(
    rows: list[RuntimeRow], run_type: RuntimeRunType | None, status: str | None, since: str | None, until: str | None
) -> list[RuntimeRow]:
    if run_type is not None or status not in (None, ""):
        return []
    return [row for row in rows if _matches_date(row, since, until)]


def _matches_status(row: RuntimeRow, run_type: RuntimeRunType, status: str | None) -> bool:
    if status is None or status == "":
        return True
    return row_status(row, run_type).lower() == status.lower()


def _matches_date(row: RuntimeRow, since: str | None, until: str | None) -> bool:
    timestamp = _row_timestamp(row)
    if timestamp is None:
        return since in (None, "") and until in (None, "")
    if since and timestamp < since:
        return False
    if until and timestamp > until:
        return False
    return True


def _row_timestamp(row: RuntimeRow) -> str | None:
    for key in ("created_at", "started_at", "failed_at", "completed_at", "published_at", "updated_at"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _investigation_summary(run_type: RuntimeRunType, run_id: str, status: str, message: str | None) -> str:
    base = f"{run_type} run {run_id} is {status}"
    return f"{base}: {message}" if message else base


def _suggested_actions(run_type: RuntimeRunType, run_id: str, status: str) -> list[str]:
    if "fail" not in status.lower() and run_type != "dead_letter":
        return []
    if run_type == "transform":
        return [f"flite transform retry {run_id}", f"POST /api/operations/runs/transform/{run_id}/retry"]
    if run_type == "index":
        return [f"flite index replay-run {run_id}", f"POST /api/operations/runs/index/{run_id}/replay"]
    if run_type == "dead_letter":
        return [f"flite outbox retry {run_id}", f"POST /api/operations/dead-letter-events/{run_id}/retry"]
    if run_type == "workflow":
        return [f"GET /api/operations/workflows/{run_id}", f"GET /api/operations/runs/workflow/{run_id}"]
    if run_type == "sync":
        return ["fix the source input, then rerun the dataset upload"]
    return ["inspect errorMessage, references, and related audit/outbox evidence"]


def relation_ids(row: RuntimeRow) -> list[str]:
    """Return the correlation ids on a run row used to fetch its related evidence."""
    return sorted({value for key, value in row.items() if _is_relation_key(key) and isinstance(value, str) and value})


def _is_relation_key(key: str) -> bool:
    # ``tenant_id`` scopes the snapshot, not a correlation: every row in a
    # tenant shares it, so treating it as a relation key matched all evidence.
    if key == "tenant_id":
        return False
    return key.endswith("_id") or key in {"id", "correlation_id"}


def _index_run_links(
    snapshot: RuntimeRunSnapshot,
    source_dataset_version_id: str,
    object_type_api_name: str,
) -> list[RuntimeRunLink]:
    links: list[RuntimeRunLink] = []
    for row in snapshot["indexRuns"]:
        source_ref = row.get("source_ref")
        if row.get("object_type_api_name") != object_type_api_name or not isinstance(source_ref, Mapping):
            continue
        if source_ref.get("dataset_version_id") == source_dataset_version_id:
            links.append(_run_link("index", row, "indexed_from", "dataset_version", source_dataset_version_id))
    return links


def _cdc_index_run_links(
    snapshot: RuntimeRunSnapshot,
    source_dataset_version_id: str,
    object_type_api_name: str,
) -> list[RuntimeRunLink]:
    event_id = _cdc_event_id(source_dataset_version_id)
    if event_id is None:
        return []
    return [
        _run_link("index", row, "cdc_event_indexed_from", "cdc_event", event_id)
        for row in snapshot["indexRuns"]
        if row.get("object_type_api_name") == object_type_api_name and _index_row_mentions_cdc_event(row, event_id)
    ]


def _index_row_mentions_cdc_event(row: RuntimeRow, event_id: str) -> bool:
    cursor = row.get("cursor")
    if not isinstance(cursor, Mapping):
        return False
    late_event_ids = cursor.get("lateEventIds")
    if isinstance(late_event_ids, list) and event_id in late_event_ids:
        return True
    return cursor.get("last_event_id") == event_id


def _cdc_event_id(source_dataset_version_id: str) -> str | None:
    event_id = source_dataset_version_id.removeprefix("cdc:")
    return event_id if event_id != source_dataset_version_id and event_id else None


def relation_resource_ids(source_dataset_version_id: str, lineage_rows: list[LineageEdgeRow]) -> list[str]:
    resource_ids: set[str] = set()
    for row in lineage_rows:
        if row["to_resource_id"] == source_dataset_version_id:
            resource_ids.add(row["from_resource_id"])
        if row["from_resource_id"] == source_dataset_version_id:
            resource_ids.add(row["to_resource_id"])
    return [source_dataset_version_id, *sorted(resource_ids - {source_dataset_version_id})]


def _producer_run_links(snapshot: RuntimeRunSnapshot, resource_id: str, relation: str) -> list[RuntimeRunLink]:
    links = [
        _run_link("sync", row, relation, "dataset_version", resource_id)
        for row in snapshot["syncRuns"]
        if row.get("committed_version_id") == resource_id
    ]
    links += [
        _run_link("transform", row, relation, "dataset_version", resource_id)
        for row in snapshot["transformRuns"]
        if row.get("output_version_id") == resource_id
    ]
    links += [
        _run_link("materialization", row, relation, "dataset_version", resource_id)
        for row in snapshot["materializationRuns"]
        if row.get("target_dataset_version_id") == resource_id
    ]
    return links


def _lineage_run_links(
    snapshot: RuntimeRunSnapshot,
    source_dataset_version_id: str,
    lineage_rows: list[LineageEdgeRow],
) -> list[RuntimeRunLink]:
    links: list[RuntimeRunLink] = []
    for row in lineage_rows:
        run_type = _run_type_for_id(snapshot, row["created_by_run_id"])
        if run_type is None:
            continue
        resource_id = (
            row["from_resource_id"] if row["to_resource_id"] == source_dataset_version_id else row["to_resource_id"]
        )
        links.append(
            _run_link(
                run_type,
                {"id": row["created_by_run_id"]},
                "lineage_created",
                row["from_resource_type"],
                resource_id,
            )
        )
    return links


def _run_type_for_id(snapshot: RuntimeRunSnapshot, run_id: str) -> RuntimeRunType | None:
    for run_type, group in _lineage_run_groups(snapshot):
        if any(row.get("id") == run_id for row in group):
            return run_type
    return None


def _lineage_run_groups(snapshot: RuntimeRunSnapshot) -> tuple[tuple[RuntimeRunType, list[RuntimeRow]], ...]:
    return (
        ("sync", snapshot["syncRuns"]),
        ("transform", snapshot["transformRuns"]),
        ("index", snapshot["indexRuns"]),
        ("materialization", snapshot["materializationRuns"]),
        ("workflow", snapshot["workflowRuns"]),
    )


def _run_link(
    run_type: RuntimeRunType,
    row: RuntimeRow,
    relation: str,
    resource_type: str,
    resource_id: str,
) -> RuntimeRunLink:
    run_id = str(row["id"])
    return {
        "runType": run_type,
        "runId": run_id,
        "relation": relation,
        "resourceType": resource_type,
        "resourceId": resource_id,
        "status": row_status(row, run_type),
        "operationPath": f"/api/operations/runs/{run_type}/{run_id}",
    }


def _dedupe_run_links(links: list[RuntimeRunLink]) -> list[RuntimeRunLink]:
    seen: set[tuple[str, str]] = set()
    deduped: list[RuntimeRunLink] = []
    for link in links:
        key = (link["runType"], link["runId"])
        if key not in seen:
            seen.add(key)
            deduped.append(link)
    return deduped
