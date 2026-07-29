"""Deterministic async dispatch for no-commit Pipeline preview runs."""

from __future__ import annotations

from collections.abc import Callable
from threading import Event
from time import monotonic

from foundry_lite.application.ports.pipeline_dag_orchestrator import (
    PipelineDagDispatchRequest,
    PipelineDagOrchestrator,
)
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineExecutionRepository,
    PipelinePreviewRunRow,
)
from foundry_lite.application.ports.transaction_context import TransactionManager
from foundry_lite.domain.context import RequestContext

_PREVIEW_TERMINAL_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})


def dispatch_pipeline_preview(
    engine: TransactionManager,
    repository: PipelineExecutionRepository,
    orchestrator: PipelineDagOrchestrator,
    ctx: RequestContext,
    row: PipelinePreviewRunRow,
) -> PipelinePreviewRunRow:
    """Dispatch a queued no-commit preview using the shared DAG boundary."""

    if row["status"] != "QUEUED" or row["dispatch_status"] == "dispatched":
        return row
    result = orchestrator.dispatch(_preview_dispatch_request(ctx, row))
    dispatch_status = "dispatched" if result.status != "unknown" else "unknown"
    error: dict[str, object] | None = None if dispatch_status == "dispatched" else {"kind": "dispatch_unknown"}
    with engine.begin() as transaction:
        updated = repository.update_preview_dispatch(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            preview_run_id=str(row["id"]),
            workflow_run_id=result.workflow_run_id,
            dispatch_status=dispatch_status,
            dispatch_error=error,
        )
    return updated or row


def _preview_dispatch_request(
    ctx: RequestContext,
    row: PipelinePreviewRunRow,
) -> PipelineDagDispatchRequest:
    return PipelineDagDispatchRequest(
        tenant_id=ctx.tenant_id,
        run_id=str(row["id"]),
        pipeline_id=str(row["pipeline_id"]),
        version_id=str(row["branch_id"]),
        request_id=ctx.request_id,
        idempotency_key=str(row["idempotency_key"]),
        execution_plan={
            "previewGraph": row["graph"],
            "graphFingerprint": row["graph_fingerprint"],
            "limits": row["limits"],
        },
        target_node_ids=tuple([row["target_node_id"]] if row["target_node_id"] else ()),
        is_commit_forbidden=True,
    )


def wait_for_preview_terminal(load: Callable[[], dict[str, object]]) -> dict[str, object]:
    """Wait briefly for a local worker that already owns preview execution."""
    deadline = monotonic() + 30
    current = load()
    while current["status"] not in _PREVIEW_TERMINAL_STATUSES and monotonic() < deadline:
        Event().wait(0.01)
        current = load()
    return current
