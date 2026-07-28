"""Atomic terminal-state persistence for lease-fenced Pipeline runs."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import PipelineExecutionLeaseFence, TransactionContext, TransactionManager
from foundry_lite.application.ports.pipeline_repository import PipelineRepository, PipelineRunRow, PipelineVersionRow
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.pipeline_node_execution_evidence import PipelineNodeExecutionEvidence
from foundry_lite.application.services.pipeline_run_execution import (
    PipelineRunExecution,
    PipelineUnsuccessfulCompletion,
    legacy_output_fields,
    unsuccessful_run_completion,
)
from foundry_lite.application.services.pipeline_run_recovery import (
    PipelineExecutionLeaseLost,
    PipelineTerminalCommitError,
)
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.application.state_transitions import PIPELINE_RUN_FAILED, PIPELINE_RUN_SUCCEEDED
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound

JsonObject = dict[str, object]


def succeed_pipeline_run(
    transaction_manager: TransactionManager,
    repository: PipelineRepository,
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    row: PipelineRunRow,
    version: PipelineVersionRow,
    compiled: Mapping[str, object],
    timeline: list[JsonObject],
    outputs: list[JsonObject],
    execution_lease_guard: PipelineExecutionLeaseFence,
) -> None:
    try:
        _persist_successful_run(
            transaction_manager,
            repository,
            runtime_service,
            ctx,
            row,
            version,
            compiled,
            timeline,
            outputs,
            execution_lease_guard,
        )
    except PipelineExecutionLeaseLost:
        raise
    except Exception as exc:
        raise PipelineTerminalCommitError("pipeline success terminal transaction failed") from exc


def _persist_successful_run(
    transaction_manager: TransactionManager,
    repository: PipelineRepository,
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    row: PipelineRunRow,
    version: PipelineVersionRow,
    compiled: Mapping[str, object],
    timeline: list[JsonObject],
    outputs: list[JsonObject],
    execution_lease_guard: PipelineExecutionLeaseFence,
) -> None:
    output_dataset_ref, output_version_id = legacy_output_fields(compiled, outputs)
    with transaction_manager.begin() as transaction:
        execution_lease_guard.require_active(transaction)
        after = repository.update_run_terminal(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            run_id=str(row["id"]),
            transition=PIPELINE_RUN_SUCCEEDED,
            output_dataset_ref=output_dataset_ref,
            output_version_id=output_version_id,
            outputs=outputs,
            timeline=[*timeline, {"event": "pipeline.run.succeeded", "at": _now()}],
            error=None,
            completed_at=_now(),
        )
        if after is None:
            raise ConflictDetected("pipeline run success terminal state changed concurrently")
        _audit_terminal(
            runtime_service,
            transaction,
            ctx,
            "succeeded",
            str(row["id"]),
            {"version_id": version["id"]},
        )


def complete_unsuccessful_pipeline_run(
    transaction_manager: TransactionManager,
    repository: PipelineRepository,
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    row: PipelineRunRow,
    compiled: Mapping[str, object],
    timeline: list[JsonObject],
    execution: PipelineRunExecution,
    evidence: PipelineNodeExecutionEvidence,
    execution_lease_guard: PipelineExecutionLeaseFence,
) -> None:
    state = unsuccessful_run_completion(runtime_service, ctx, row, compiled, timeline, execution)
    try:
        _persist_unsuccessful_run(
            transaction_manager,
            repository,
            runtime_service,
            ctx,
            row,
            execution,
            evidence,
            execution_lease_guard,
            state,
        )
    except PipelineExecutionLeaseLost:
        raise
    except Exception as exc:
        raise PipelineTerminalCommitError("pipeline terminal evidence transaction failed") from exc


def fail_pipeline_run(
    transaction_manager: TransactionManager,
    repository: PipelineRepository,
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    row: PipelineRunRow,
    exc: Exception,
) -> None:
    run_id = str(row["id"])
    error = runtime_service._error_payload(exc, ctx, run_id=run_id)
    with transaction_manager.begin() as transaction:
        current = repository.run_by_id(transaction=transaction, tenant_id=ctx.tenant_id, run_id=run_id)
        if current is None:
            raise NotFound("pipeline run not found", details={"run_id": run_id})
        after = repository.update_run_terminal(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            run_id=run_id,
            transition=PIPELINE_RUN_FAILED,
            output_dataset_ref=current["output_dataset_ref"],
            output_version_id=current["output_version_id"],
            outputs=list(current["outputs"]),
            timeline=[*current["timeline"], {"event": "pipeline.run.failed", "at": _now(), "error": dict(error)}],
            error=dict(error),
            completed_at=_now(),
        )
        if after is not None:
            _audit_terminal(runtime_service, transaction, ctx, "failed", run_id, {"error": dict(error)})


def _persist_unsuccessful_run(
    transaction_manager: TransactionManager,
    repository: PipelineRepository,
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    row: PipelineRunRow,
    execution: PipelineRunExecution,
    evidence: PipelineNodeExecutionEvidence,
    execution_lease_guard: PipelineExecutionLeaseFence,
    state: PipelineUnsuccessfulCompletion,
) -> None:
    with transaction_manager.begin() as transaction:
        execution_lease_guard.require_active(transaction)
        evidence.fail_and_skip(
            transaction,
            failed_attempt=execution.failed_attempt,
            failed_item=execution.failed_item,
            skipped_items=execution.skipped_items,
            error=state.error,
        )
        after = repository.update_run_terminal(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            run_id=str(row["id"]),
            transition=state.transition,
            output_dataset_ref=state.output_dataset_ref,
            output_version_id=state.output_version_id,
            outputs=list(state.outputs),
            timeline=list(state.timeline),
            error=state.error,
            completed_at=_now(),
        )
        if after is None:
            raise ConflictDetected("pipeline run terminal state changed concurrently")
        _audit_terminal(runtime_service, transaction, ctx, state.status, str(row["id"]), {"outputs": state.outputs})


def _audit_terminal(
    runtime_service: RuntimeEvidenceBoundary,
    transaction: TransactionContext,
    ctx: RequestContext,
    status: str,
    run_id: str,
    after_ref: Mapping[str, object],
) -> None:
    runtime_service._audit(
        transaction,
        ctx,
        event_type=f"pipeline.{status}",
        resource_type="pipeline_run",
        resource_id=run_id,
        action=status,
        after_ref=after_ref,
    )
