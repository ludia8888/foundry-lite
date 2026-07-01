"""Application service helpers for transform record dlq workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import (
    DatasetTransactionRepository,
    DeadLetterRecord,
    TransactionContext,
    TransformDeadLetterRecord,
    TransformExecutionResult,
)
from foundry_lite.application.primitives import _json_hash, _new_id, _now
from foundry_lite.application.services.runtime_error_payloads import scrub_error_text
from foundry_lite.application.services.transform_protocols import TransformRuntimeBoundary
from foundry_lite.application.services.transform_runs import TransformRunPlan
from foundry_lite.domain.context import RequestContext

TRANSFORM_RECORD_DLQ_KIND = "transform_execution"


def persist_transform_dead_letters(
    *,
    repository: DatasetTransactionRepository,
    runtime_service: TransformRuntimeBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    plan: TransformRunPlan,
    execution: TransformExecutionResult,
) -> tuple[str, ...]:
    failed_at = _now()
    inserted_ids: list[str] = []
    for dead_letter in execution.dead_letters:
        record = transform_dead_letter_record(ctx, plan, dead_letter, failed_at)
        inserted = repository.insert_dead_letter_record(transaction=conn, record=record)
        if inserted:
            inserted_ids.append(record.dead_letter_record_id)
            audit_transform_dead_letter(runtime_service, conn, ctx, plan, record, dead_letter)
    return tuple(inserted_ids)


def transform_dead_letter_summary(
    execution: TransformExecutionResult,
    inserted_ids: Sequence[str],
) -> Mapping[str, object]:
    return {
        "transformRecordDlq": {
            "quarantinedRows": len(execution.dead_letters),
            "deadLetterRecordIds": list(inserted_ids),
            "policy": "quarantine_and_fix_transform_or_input_then_rerun",
        }
    }


def transform_dead_letter_record(
    ctx: RequestContext,
    plan: TransformRunPlan,
    dead_letter: TransformDeadLetterRecord,
    failed_at: str,
) -> DeadLetterRecord:
    payload_hash = _json_hash(dead_letter.payload)
    return DeadLetterRecord(
        dead_letter_record_id=_new_id("dlqr"),
        tenant_id=ctx.tenant_id,
        source_event_id=_source_event_id(plan, dead_letter, payload_hash),
        source_dataset_version_id=plan.input_versions.get(dead_letter.input_dataset_ref),
        source_run_id=plan.run_id,
        payload=dict(dead_letter.payload),
        payload_hash=payload_hash,
        schema_version=None,
        transform_version=_transform_source_hash(plan),
        error_kind=dead_letter.error_kind,
        error_message=scrub_error_text(dead_letter.error_message),
        event_time=None,
        ingested_at=failed_at,
        first_failed_at=failed_at,
        attempts=1,
        status="QUARANTINED",
        replay_status="NOT_REQUESTED",
        replay_run_id=None,
        is_closed_partition_affected=False,
        metadata=_dead_letter_metadata(plan, dead_letter),
    )


def audit_transform_dead_letter(
    runtime_service: TransformRuntimeBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    plan: TransformRunPlan,
    record: DeadLetterRecord,
    dead_letter: TransformDeadLetterRecord,
) -> None:
    runtime_service._audit(
        conn,
        ctx,
        event_type="dead_letter_record.quarantined",
        resource_type="dead_letter_record",
        resource_id=record.dead_letter_record_id,
        action="transform_record_quarantine",
        after_ref={
            "transformRunId": plan.run_id,
            "inputDatasetRef": dead_letter.input_dataset_ref,
            "rowIndex": dead_letter.row_index,
            "status": record.status,
        },
        correlation_id=plan.run_id,
    )


def _dead_letter_metadata(plan: TransformRunPlan, dead_letter: TransformDeadLetterRecord) -> Mapping[str, object]:
    input_version_id = plan.input_versions.get(dead_letter.input_dataset_ref)
    return {
        "recordDlqKind": TRANSFORM_RECORD_DLQ_KIND,
        "transformId": plan.transform_id,
        "transformRunId": plan.run_id,
        "transformApiName": str(plan.definition_snapshot["api_name"]),
        "language": plan.language,
        "rowIndex": dead_letter.row_index,
        "inputDatasetRef": dead_letter.input_dataset_ref,
        "inputDatasetVersionId": input_version_id,
        "outputDatasetRef": str(plan.definition_snapshot["output_dataset_ref"]),
        "outputTransactionId": plan.transaction_id,
        "remediationPolicy": "fix_transform_or_input_then_rerun",
    }


def _source_event_id(plan: TransformRunPlan, dead_letter: TransformDeadLetterRecord, payload_hash: str) -> str:
    return f"transform:{plan.run_id}:{dead_letter.input_dataset_ref}:{dead_letter.row_index}:{payload_hash[:12]}"


def _transform_source_hash(plan: TransformRunPlan) -> str | None:
    for key in ("python_source_sha256", "sql_template_sha256"):
        value = plan.definition_snapshot.get(key)
        if isinstance(value, str) and value:
            return value
    return None
