"""Audit, outbox, and policy evidence helpers for Pipeline scheduling."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.pipeline_repository import PipelineScheduleRow
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext


def record_pipeline_schedule_event(
    runtime_service: RuntimeEvidenceBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    row: PipelineScheduleRow | None,
    event: str,
    result: Mapping[str, object],
    idempotency_key: str,
) -> None:
    pipeline_id = str(row["pipeline_id"]) if row is not None else "unknown"
    schedule_id = str(row["id"]) if row is not None else pipeline_id
    runtime_service._audit(
        conn,
        ctx,
        event_type=f"pipeline.schedule.{event}",
        resource_type="pipeline_schedule",
        resource_id=schedule_id,
        action=f"pipeline:schedule:{event}",
        after_ref=result,
        correlation_id=idempotency_key,
    )
    runtime_service._outbox(
        conn,
        ctx,
        f"pipeline.schedule.{event}",
        "pipeline_schedule",
        schedule_id,
        {"pipelineId": pipeline_id, **dict(result)},
        idempotency_key=idempotency_key,
        correlation_id=idempotency_key,
    )


def require_pipeline_schedule_write(
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    operation: str,
    resource_id: str,
) -> None:
    permission = "pipeline:run" if operation == "tick_pipeline_schedules" else "pipeline:deploy"
    runtime_service._require_or_audit(ctx, permission, "pipeline_schedule", resource_id)
    runtime_service._require_write_traffic_open(
        ctx,
        operation=operation,
        resource_type="pipeline_schedule",
        resource_id=resource_id,
    )
