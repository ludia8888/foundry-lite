"""Resolve terminal Pipeline outputs whose Media commit acknowledgement was lost."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.ports.dataset_transaction_repository import (
    DatasetTransactionRepository,
)
from foundry_lite.application.ports.media_repository import MediaRepository
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineExecutionRepository,
)
from foundry_lite.application.ports.pipeline_repository import PipelineRepository, PipelineRunRow
from foundry_lite.application.ports.transaction_context import TransactionContext, TransactionManager
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.pipeline_run_output_recovery import (
    RecoveredPipelineOutput,
    committed_pipeline_outputs,
    reconciled_pipeline_output,
    single_serving_dataset_fields,
)
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.application.state_transitions import (
    PIPELINE_RUN_RECONCILED_FAILED,
    PIPELINE_RUN_RECONCILED_PARTIAL,
    PIPELINE_RUN_RECONCILED_SUCCEEDED,
    StatusTransition,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation
from foundry_lite.observability.logging import log_event

JsonObject = dict[str, object]
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineUnknownCommitResolution:
    transition: StatusTransition
    status: str
    outputs: tuple[JsonObject, ...]
    output_dataset_ref: str | None
    output_version_id: str | None
    error: JsonObject | None


def has_unknown_commit_output(row: Mapping[str, object]) -> bool:
    outputs = row.get("outputs")
    return isinstance(outputs, list) and any(
        isinstance(output, Mapping) and output.get("status") == "COMMIT_OUTCOME_UNKNOWN" for output in outputs
    )


def reconcile_unknown_commit_outputs(
    transaction_manager: TransactionManager,
    pipeline_repository: PipelineRepository,
    execution_repository: PipelineExecutionRepository,
    dataset_transaction_repository: DatasetTransactionRepository,
    media_repository: MediaRepository,
    media_transaction_service: MediaTransactionService,
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    row: PipelineRunRow,
) -> bool:
    transaction_ids = _unknown_transaction_ids(row)
    if not transaction_ids:
        return False
    resolution = _load_resolution(
        transaction_manager,
        execution_repository,
        dataset_transaction_repository,
        media_repository,
        media_transaction_service,
        runtime_service,
        ctx,
        row,
        transaction_ids,
    )
    if resolution is None:
        return False
    return _persist_resolution(
        transaction_manager,
        pipeline_repository,
        runtime_service,
        ctx,
        row,
        resolution,
    )


def _load_resolution(
    transaction_manager: TransactionManager,
    execution_repository: PipelineExecutionRepository,
    dataset_transaction_repository: DatasetTransactionRepository,
    media_repository: MediaRepository,
    media_transaction_service: MediaTransactionService,
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    row: PipelineRunRow,
    transaction_ids: tuple[str, ...],
) -> PipelineUnknownCommitResolution | None:
    statuses = _resolved_transaction_statuses(
        transaction_manager,
        media_repository,
        media_transaction_service,
        runtime_service,
        ctx,
        row,
        transaction_ids,
    )
    if statuses is None:
        return None
    return _resolution(
        transaction_manager,
        execution_repository,
        dataset_transaction_repository,
        media_repository,
        runtime_service,
        ctx,
        row,
        statuses,
    )


def _resolved_transaction_statuses(
    transaction_manager: TransactionManager,
    media_repository: MediaRepository,
    media_transaction_service: MediaTransactionService,
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    row: PipelineRunRow,
    transaction_ids: tuple[str, ...],
) -> dict[str, str] | None:
    statuses = _transaction_statuses(transaction_manager, media_repository, runtime_service, ctx, row, transaction_ids)
    if statuses is None:
        return None
    for transaction_id, status in statuses.items():
        if status == "OPEN" and not _abort_open_transaction(
            transaction_manager, media_transaction_service, runtime_service, ctx, row, transaction_id
        ):
            return None
    return _transaction_statuses(transaction_manager, media_repository, runtime_service, ctx, row, transaction_ids)


def _abort_open_transaction(
    transaction_manager: TransactionManager,
    service: MediaTransactionService,
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    row: PipelineRunRow,
    transaction_id: str,
) -> bool:
    try:
        service.abort(
            ctx,
            media_transaction_id=transaction_id,
            error={"code": "PIPELINE_COMMIT_OUTCOME_RECONCILIATION"},
        )
    except ConflictDetected:
        return True
    except Exception as exc:
        _record_reconciliation_deferred(
            transaction_manager,
            runtime_service,
            ctx,
            row,
            "abort_open_transaction",
            exc,
            transaction_id=transaction_id,
        )
        return False
    return True


def _transaction_statuses(
    transaction_manager: TransactionManager,
    repository: MediaRepository,
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    row: PipelineRunRow,
    transaction_ids: tuple[str, ...],
) -> dict[str, str] | None:
    try:
        with transaction_manager.begin() as transaction:
            return {
                transaction_id: (
                    record.status
                    if (
                        record := repository.transaction_by_id(
                            transaction=transaction,
                            tenant_id=ctx.tenant_id,
                            media_transaction_id=transaction_id,
                        )
                    )
                    is not None
                    else "MISSING"
                )
                for transaction_id in transaction_ids
            }
    except Exception as exc:
        _record_reconciliation_deferred(
            transaction_manager, runtime_service, ctx, row, "load_transaction_statuses", exc
        )
        return None


def _resolution(
    transaction_manager: TransactionManager,
    execution_repository: PipelineExecutionRepository,
    dataset_transaction_repository: DatasetTransactionRepository,
    media_repository: MediaRepository,
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    row: PipelineRunRow,
    statuses: Mapping[str, str],
) -> PipelineUnknownCommitResolution | None:
    recovered = _recovered_outputs(
        transaction_manager,
        execution_repository,
        dataset_transaction_repository,
        media_repository,
        runtime_service,
        ctx,
        str(row["id"]),
        row,
    )
    if recovered is None:
        return None
    committed_ids = {_media_transaction_id(output) for output in recovered}
    are_unknowns_committed = all(status == "COMMITTED" for status in statuses.values())
    if are_unknowns_committed and set(statuses) - committed_ids:
        return None
    failed_outputs = _resolved_failed_outputs(row, statuses)
    if are_unknowns_committed and not failed_outputs:
        return _successful_resolution(recovered)
    return _unsuccessful_resolution(
        runtime_service,
        ctx,
        row,
        statuses,
        recovered,
        failed_outputs,
    )


def _recovered_outputs(
    transaction_manager: TransactionManager,
    execution_repository: PipelineExecutionRepository,
    dataset_transaction_repository: DatasetTransactionRepository,
    media_repository: MediaRepository,
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    run_id: str,
    row: PipelineRunRow,
) -> list[RecoveredPipelineOutput] | None:
    try:
        with transaction_manager.begin() as transaction:
            return committed_pipeline_outputs(
                transaction,
                execution_repository,
                dataset_transaction_repository,
                media_repository,
                ctx,
                run_id,
            )
    except Exception as exc:
        _record_reconciliation_deferred(transaction_manager, runtime_service, ctx, row, "load_committed_outputs", exc)
        return None


def _record_reconciliation_deferred(
    transaction_manager: TransactionManager,
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    row: PipelineRunRow,
    stage: str,
    exc: Exception,
    *,
    transaction_id: str | None = None,
) -> None:
    error = dict(runtime_service._error_payload(exc, ctx, run_id=str(row["id"])))
    evidence: JsonObject = {"stage": stage, "error": error}
    if transaction_id is not None:
        evidence["mediaTransactionId"] = transaction_id
    try:
        with transaction_manager.begin() as transaction:
            runtime_service._audit(
                transaction,
                ctx,
                event_type="pipeline.commit_outcome_reconciliation_deferred",
                resource_type="pipeline_run",
                resource_id=str(row["id"]),
                action="commit_outcome_reconciliation",
                decision="deny",
                after_ref=evidence,
            )
    except Exception as audit_exc:
        log_event(
            _LOGGER,
            "pipeline.commit_outcome_reconciliation_evidence_failed",
            level=logging.ERROR,
            tenant_id=ctx.tenant_id,
            request_id=ctx.request_id,
            run_id=str(row["id"]),
            stage=stage,
            exception_type=type(exc).__name__,
            audit_exception_type=type(audit_exc).__name__,
        )


def _successful_resolution(
    recovered: list[RecoveredPipelineOutput],
) -> PipelineUnknownCommitResolution:
    dataset_ref, version_id = single_serving_dataset_fields(recovered)
    return PipelineUnknownCommitResolution(
        transition=PIPELINE_RUN_RECONCILED_SUCCEEDED,
        status="succeeded",
        outputs=tuple(reconciled_pipeline_output(output) for output in recovered),
        output_dataset_ref=dataset_ref,
        output_version_id=version_id,
        error=None,
    )


def _unsuccessful_resolution(
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    row: PipelineRunRow,
    statuses: Mapping[str, str],
    recovered: list[RecoveredPipelineOutput],
    failed_outputs: tuple[JsonObject, ...],
) -> PipelineUnknownCommitResolution:
    transition = PIPELINE_RUN_RECONCILED_PARTIAL if recovered else PIPELINE_RUN_RECONCILED_FAILED
    status = "partial" if recovered else "failed"
    message = (
        "pipeline output commit reconciled with failed outputs"
        if all(value == "COMMITTED" for value in statuses.values())
        else "pipeline output commit reconciled as not committed"
    )
    error = runtime_service._error_payload(
        InvariantViolation(
            message,
            details={"run_id": row["id"], "media_transaction_statuses": dict(statuses)},
        ),
        ctx,
        run_id=str(row["id"]),
    )
    dataset_ref, version_id = single_serving_dataset_fields(recovered)
    return PipelineUnknownCommitResolution(
        transition,
        status,
        (
            *(reconciled_pipeline_output(output) for output in recovered),
            *failed_outputs,
        ),
        dataset_ref,
        version_id,
        dict(error),
    )


def _resolved_failed_outputs(
    row: PipelineRunRow,
    statuses: Mapping[str, str],
) -> tuple[JsonObject, ...]:
    outputs: list[JsonObject] = []
    for output in row["outputs"]:
        if output.get("status") == "FAILED":
            outputs.append(dict(output))
        elif output.get("status") == "COMMIT_OUTCOME_UNKNOWN":
            failed = _failed_unknown_output(output, statuses)
            if failed is not None:
                outputs.append(failed)
    return tuple(outputs)


def _failed_unknown_output(
    output: Mapping[str, object],
    statuses: Mapping[str, str],
) -> JsonObject | None:
    transaction_id = _unknown_transaction_id(output)
    status = statuses.get(str(transaction_id))
    if status is None or status == "COMMITTED":
        return None
    failed = dict(output)
    failed["status"] = "FAILED"
    failed["isServing"] = False
    failed["manifest"] = _failed_unknown_manifest(output, status)
    failed["artifactEvidence"] = _failed_unknown_evidence(output, status)
    return failed


def _failed_unknown_manifest(
    output: Mapping[str, object],
    transaction_status: str,
) -> JsonObject:
    manifest = output.get("manifest")
    return {
        **(dict(manifest) if isinstance(manifest, Mapping) else {}),
        "commitOutcome": "NOT_COMMITTED",
        "transactionStatus": transaction_status,
    }


def _failed_unknown_evidence(
    output: Mapping[str, object],
    transaction_status: str,
) -> JsonObject:
    evidence = output.get("artifactEvidence")
    return {
        **(dict(evidence) if isinstance(evidence, Mapping) else {}),
        "status": "RECONCILED_FAILED",
        "transactionStatus": transaction_status,
    }


def _persist_resolution(
    transaction_manager: TransactionManager,
    repository: PipelineRepository,
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    row: PipelineRunRow,
    resolution: PipelineUnknownCommitResolution,
) -> bool:
    if row["completed_at"] is None:
        return False
    event: JsonObject = {
        "event": "pipeline.run.commit_outcome_reconciled",
        "at": _now(),
        "status": resolution.status,
    }
    timeline: list[JsonObject] = [
        *row["timeline"],
        event,
    ]
    with transaction_manager.begin() as transaction:
        after = repository.update_run_terminal(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            run_id=str(row["id"]),
            transition=resolution.transition,
            output_dataset_ref=resolution.output_dataset_ref,
            output_version_id=resolution.output_version_id,
            outputs=list(resolution.outputs),
            timeline=timeline,
            error=resolution.error,
            completed_at=_now(),
            expected_completed_at=row["completed_at"],
        )
        if after is not None:
            _audit_resolution(runtime_service, transaction, ctx, row, resolution)
    return after is not None


def _audit_resolution(
    runtime_service: RuntimeEvidenceBoundary,
    transaction: TransactionContext,
    ctx: RequestContext,
    row: PipelineRunRow,
    resolution: PipelineUnknownCommitResolution,
) -> None:
    runtime_service._audit(
        transaction,
        ctx,
        event_type="pipeline.commit_outcome_reconciled",
        resource_type="pipeline_run",
        resource_id=str(row["id"]),
        action="commit_outcome_reconciled",
        after_ref={
            "status": resolution.status,
            "outputs": list(resolution.outputs),
            "error": resolution.error,
        },
    )


def _unknown_transaction_ids(row: Mapping[str, object]) -> tuple[str, ...]:
    outputs = row.get("outputs")
    if not isinstance(outputs, list):
        return ()
    values = [_unknown_transaction_id(output) for output in outputs]
    return tuple(dict.fromkeys(value for value in values if isinstance(value, str) and value))


def _unknown_transaction_id(output: object) -> object:
    if not isinstance(output, Mapping) or output.get("status") != "COMMIT_OUTCOME_UNKNOWN":
        return None
    reference = output.get("ref")
    return reference.get("mediaTransactionId") if isinstance(reference, Mapping) else None


def _media_transaction_id(output: RecoveredPipelineOutput) -> str:
    value = output.ref.get("mediaTransactionId")
    return value if isinstance(value, str) else ""
