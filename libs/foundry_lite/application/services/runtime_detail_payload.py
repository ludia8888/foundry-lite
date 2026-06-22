from __future__ import annotations

from foundry_lite.application.ports import (
    DatasetCheckResultRow,
    LineageEdgeRow,
    RuntimeJsonObject,
    RuntimeRow,
    RuntimeRunDetail,
    RuntimeRunInvestigation,
    RuntimeRunRelationRow,
    RuntimeRunType,
)
from foundry_lite.application.services.runtime_quality_report import quality_report_for_transaction
from foundry_lite.application.services.runtime_run_queries import (
    correlation_id,
    error_message,
    late_data_detail,
    materialization_detail,
    row_references,
    row_status,
    run_investigation,
)


def runtime_run_detail_payload(
    *,
    parsed_type: RuntimeRunType,
    run_id: str,
    row: RuntimeRow,
    outbox_events: list[RuntimeRow],
    audit_events: list[RuntimeRow],
    object_edits: list[RuntimeRow],
    action_writebacks: list[RuntimeRow],
    run_relations: list[RuntimeRunRelationRow],
    lineage_edges: list[LineageEdgeRow],
    dataset_transaction: RuntimeRow | None,
    downstream_impact: RuntimeJsonObject | None,
    quality_check_results: list[DatasetCheckResultRow],
    quality_failed_row_samples: list[RuntimeRow],
) -> RuntimeRunDetail:
    quality_report = _detail_quality_report(dataset_transaction, quality_check_results, quality_failed_row_samples)
    return {
        "runType": parsed_type,
        "runId": run_id,
        "row": row,
        "status": row_status(row),
        "errorMessage": error_message(row.get("error")),
        "error": row.get("error"),
        "correlationId": correlation_id(row),
        "references": row_references(row),
        "investigation": _detail_investigation(
            parsed_type, row, outbox_events, audit_events, object_edits, action_writebacks, lineage_edges
        ),
        "relatedOutboxEvents": outbox_events,
        "relatedAuditEvents": audit_events,
        "relatedObjectEdits": object_edits,
        "relatedActionWritebacks": action_writebacks,
        "runRelations": run_relations,
        "lineageEdges": lineage_edges,
        "datasetTransaction": dataset_transaction,
        "lateData": late_data_detail(dataset_transaction),
        "materialization": materialization_detail(row, dataset_transaction),
        "downstreamImpact": downstream_impact,
        "quality": quality_report,
    }


def _detail_investigation(
    parsed_type: RuntimeRunType,
    row: RuntimeRow,
    outbox_events: list[RuntimeRow],
    audit_events: list[RuntimeRow],
    object_edits: list[RuntimeRow],
    action_writebacks: list[RuntimeRow],
    lineage_edges: list[LineageEdgeRow],
) -> RuntimeRunInvestigation:
    return run_investigation(
        row,
        parsed_type,
        related_outbox_count=len(outbox_events),
        related_audit_count=len(audit_events),
        related_object_edit_count=len(object_edits),
        related_action_writeback_count=len(action_writebacks),
        lineage_edge_count=len(lineage_edges),
    )


def _detail_quality_report(
    dataset_transaction: RuntimeRow | None,
    check_results: list[DatasetCheckResultRow],
    failed_row_samples: list[RuntimeRow],
) -> RuntimeJsonObject | None:
    return quality_report_for_transaction(
        dataset_transaction,
        check_results,
        failed_row_samples=failed_row_samples,
    )
