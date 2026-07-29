"""Compatibility event projection helpers for asynchronous Pipeline runs."""

from __future__ import annotations

from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineExecutionRepository,
    PipelineRunEventRecord,
)
from foundry_lite.application.ports.pipeline_repository import PipelineRepository, PipelineRunRow
from foundry_lite.application.ports.transaction_context import TransactionContext, TransactionManager
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.pipeline_run_requests import required_pipeline_run
from foundry_lite.domain.context import RequestContext

_TERMINAL_STATUSES = frozenset({"succeeded", "partial", "failed", "cancelled"})


def append_legacy_terminal_event(
    engine: TransactionManager,
    pipeline_repository: PipelineRepository,
    repository: PipelineExecutionRepository,
    ctx: RequestContext,
    run_id: str,
) -> None:
    """Dual-write the legacy executor's terminal state into the event ledger."""

    with engine.begin() as transaction:
        row = required_pipeline_run(pipeline_repository, transaction, ctx, run_id)
        _append_terminal_event(repository, transaction, ctx, row)


def _append_terminal_event(
    repository: PipelineExecutionRepository,
    transaction: TransactionContext,
    ctx: RequestContext,
    row: PipelineRunRow,
) -> None:
    status = str(row["status"])
    if status not in _TERMINAL_STATUSES:
        return
    events = repository.run_events(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        run_id=str(row["id"]),
        after_sequence=0,
        limit=500,
    )
    event_type = f"pipeline.run.{status}"
    if any(event["event_type"] == event_type for event in events):
        return
    repository.append_run_event(
        transaction=transaction,
        record=PipelineRunEventRecord(
            event_id=_new_id("pevent"),
            tenant_id=ctx.tenant_id,
            run_id=str(row["id"]),
            event_type=event_type,
            payload={},
            created_at=_now(),
        ),
    )
