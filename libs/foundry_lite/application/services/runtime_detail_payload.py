from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from foundry_lite.application.ports import (
    DatasetCheckResultRow,
    DatasetQualityRepository,
    DatasetTransactionRepository,
    LineageEdgeRow,
    RuntimeJsonObject,
    RuntimeRepository,
    RuntimeRow,
    RuntimeRunDetail,
    RuntimeRunInvestigation,
    RuntimeRunRelationRow,
    RuntimeRunType,
    TransactionManager,
)
from foundry_lite.application.services.runtime_quality_report import quality_report_for_transaction
from foundry_lite.application.services.runtime_run_queries import (
    correlation_id,
    error_message,
    late_data_detail,
    materialization_detail,
    resource_ids_for_lineage,
    row_references,
    row_status,
    run_investigation,
)
from foundry_lite.domain.context import RequestContext


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
        "quality": _detail_quality_report(dataset_transaction, quality_check_results, quality_failed_row_samples),
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


def dataset_transaction_for_row(
    dataset_transaction_repository: DatasetTransactionRepository,
    engine: TransactionManager,
    ctx: RequestContext,
    row: RuntimeRow,
) -> RuntimeRow | None:
    transaction_id = row.get("transaction_id")
    version_id = row.get("target_dataset_version_id") or row.get("committed_version_id")
    with engine.begin() as conn:
        if isinstance(transaction_id, str) and transaction_id:
            transaction = dataset_transaction_repository.transaction_by_id(
                transaction=conn,
                transaction_id=transaction_id,
            )
        elif isinstance(version_id, str) and version_id:
            transaction = dataset_transaction_repository.committed_transaction_by_version(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                committed_version_id=version_id,
            )
        else:
            transaction = None
    if transaction is None or transaction["tenant_id"] != ctx.tenant_id:
        return None
    return transaction


def quality_evidence_for_transaction(
    dataset_quality_repository: DatasetQualityRepository,
    dataset_transaction_repository: DatasetTransactionRepository,
    engine: TransactionManager,
    ctx: RequestContext,
    dataset_transaction: RuntimeRow | None,
) -> tuple[list[DatasetCheckResultRow], list[RuntimeRow]]:
    if dataset_transaction is None:
        return [], []
    transaction_id = dataset_transaction.get("id")
    if not isinstance(transaction_id, str) or not transaction_id:
        return [], []
    with engine.begin() as conn:
        check_results = dataset_quality_repository.check_results_for_transaction(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            transaction_id=transaction_id,
        )
        rows = dataset_transaction_repository.list_dead_letter_records(
            transaction=conn,
            tenant_id=ctx.tenant_id,
        )
    samples = cast(list[RuntimeRow], [row for row in rows if _is_quality_sample_for_transaction(row, transaction_id)])
    return check_results, samples


def lineage_edges_for_row(
    runtime_repository: RuntimeRepository,
    ctx: RequestContext,
    row: RuntimeRow,
) -> list[LineageEdgeRow]:
    edges: list[LineageEdgeRow] = []
    seen: set[str] = set()
    for resource_id in resource_ids_for_lineage(row):
        for edge in runtime_repository.lineage_for_resource(tenant_id=ctx.tenant_id, resource_id=resource_id):
            edge_id = edge["id"]
            if edge_id not in seen:
                seen.add(edge_id)
                edges.append(edge)
    return edges


def run_relations_for_row(
    runtime_repository: RuntimeRepository,
    engine: TransactionManager,
    ctx: RequestContext,
    run_type: RuntimeRunType,
    run_id: str,
) -> list[RuntimeRunRelationRow]:
    with engine.begin() as conn:
        return runtime_repository.run_relations_for_run(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            run_type=run_type,
            run_id=run_id,
        )


def _is_quality_sample_for_transaction(row: RuntimeRow, transaction_id: str) -> bool:
    metadata = row.get("metadata")
    return (
        row.get("error_kind") == "DATA_QUALITY_CONTRACT"
        and isinstance(metadata, Mapping)
        and metadata.get("transactionId") == transaction_id
    )
