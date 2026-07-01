"""Application service helpers for workflow orchestration helpers workflows."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import (
    AdapterError,
    ProductWorkflowRun,
    WorkflowRun,
    WorkflowRunRecord,
    WorkflowRunRow,
    WorkflowStartRequest,
    workflow_request_fingerprint,
    workflow_run_id,
)
from foundry_lite.application.ports.transaction_context import (
    WORKFLOW_RUN_CANCELLED,
    WORKFLOW_RUN_FAILED,
    WORKFLOW_RUN_RUNNING,
    WORKFLOW_RUN_START_UNKNOWN,
    WORKFLOW_RUN_SUCCEEDED,
    StatusTransition,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.runtime_error_payloads import scrub_error_mapping
from foundry_lite.application.services.workflow_connector_sync import connector_sync_run_id
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected

CONNECTOR_SYNC_WORKFLOW_NAME = "ConnectorSyncWorkflow"
MEDIA_PROCESSING_WORKFLOW_NAME = "MediaProcessingWorkflow"
CONNECTOR_SYNC_WORKFLOW_DEFINITION_VERSION = "connector_sync.workflow/v1"
MEDIA_PROCESSING_WORKFLOW_DEFINITION_VERSION = "media_processing.workflow/v1"


def connector_sync_request(
    ctx: RequestContext,
    dataset_ref: str,
    connector_name: str,
    resource_name: str,
    idempotency_key: str,
    sync_name: str | None,
    config_fingerprint: str | None,
    transaction_type: str,
) -> WorkflowStartRequest:
    return WorkflowStartRequest(
        workflow_name=CONNECTOR_SYNC_WORKFLOW_NAME,
        tenant_id=ctx.tenant_id,
        request_id=ctx.request_id,
        idempotency_key=idempotency_key,
        input=_connector_sync_input(
            ctx,
            dataset_ref,
            connector_name,
            resource_name,
            idempotency_key,
            sync_name,
            config_fingerprint,
            transaction_type,
        ),
    )


def media_processing_request(
    ctx: RequestContext,
    media_item_version_id: str,
    processor_spec: Mapping[str, object],
    idempotency_key: str,
) -> WorkflowStartRequest:
    return WorkflowStartRequest(
        workflow_name=MEDIA_PROCESSING_WORKFLOW_NAME,
        tenant_id=ctx.tenant_id,
        request_id=ctx.request_id,
        idempotency_key=idempotency_key,
        input={
            "workflowKind": "media_processing",
            "workflowDefinitionVersion": MEDIA_PROCESSING_WORKFLOW_DEFINITION_VERSION,
            "mediaItemVersionId": media_item_version_id,
            "processorSpec": dict(processor_spec),
        },
    )


def workflow_record(
    ctx: RequestContext,
    request: WorkflowStartRequest,
    workflow_profile: str,
    dataset_id: str | None,
) -> WorkflowRunRecord:
    return WorkflowRunRecord(
        workflow_run_id=workflow_run_id(request),
        tenant_id=ctx.tenant_id,
        workflow_name=request.workflow_name,
        workflow_profile=workflow_profile,
        status="requested",
        idempotency_key=request.idempotency_key,
        request_fingerprint=workflow_request_fingerprint(request),
        input=dict(request.input),
        output={},
        error=None,
        dataset_id=dataset_id,
        audit_event_id=None,
        attempts=0,
        created_at=_now(),
        started_at=None,
        completed_at=None,
    )


def workflow_start_request_from_row(row: WorkflowRunRow, request: WorkflowStartRequest) -> WorkflowStartRequest:
    return WorkflowStartRequest(
        workflow_name=row["workflow_name"],
        tenant_id=row["tenant_id"],
        request_id=request.request_id,
        idempotency_key=row["idempotency_key"],
        input=dict(row["input"]),
    )


def require_same_workflow_request(row: WorkflowRunRow, request: WorkflowStartRequest) -> None:
    if row["request_fingerprint"] == workflow_request_fingerprint(request):
        return
    raise ConflictDetected(
        "workflow idempotency key already belongs to a different request",
        details={"workflow_run_id": row["id"], "workflow_name": row["workflow_name"]},
    )


def ensure_cancellable(row: WorkflowRunRow) -> None:
    if row["status"] not in {"succeeded", "failed"}:
        return
    raise ConflictDetected(
        "terminal workflow run cannot be cancelled",
        details={"workflow_run_id": row["id"], "status": row["status"]},
    )


def cancelled_ledger_run(row: WorkflowRunRow, reason: str | None, *, source: str) -> WorkflowRun:
    return WorkflowRun(
        run_id=row["id"],
        workflow_name=row["workflow_name"],
        status="cancelled",
        output=_cancelled_output(row, reason, source, cleanup_state="not_started"),
        error=None,
    )


def cancelled_adapter_run(row: WorkflowRunRow, run: WorkflowRun, reason: str | None) -> WorkflowRun:
    _require_matching_cancelled_run(row, run)
    if run.status != "cancelled":
        _ensure_cancellable_status_after_adapter_cancel(row, run)
    output = dict(run.output)
    output["cancellation"] = _cancellation_payload(reason, "workflow_adapter", cleanup_state="adapter_confirmed")
    return WorkflowRun(
        run_id=run.run_id,
        workflow_name=run.workflow_name,
        status=run.status,
        output=output,
        error=run.error,
    )


def workflow_transition_for_run(run: WorkflowRun) -> StatusTransition:
    if run.status == "succeeded":
        return WORKFLOW_RUN_SUCCEEDED
    if run.status == "cancelled":
        return WORKFLOW_RUN_CANCELLED
    if run.status in {"queued", "running"}:
        return WORKFLOW_RUN_RUNNING
    error = run.error or {}
    if error.get("kind") in {"timeout", "unavailable"} and error.get("retryable") is True:
        return WORKFLOW_RUN_START_UNKNOWN
    return WORKFLOW_RUN_FAILED


def workflow_run_from_start_exception(
    request: WorkflowStartRequest,
    adapter_profile: str,
    exc: Exception,
) -> WorkflowRun:
    return WorkflowRun(
        run_id=workflow_run_id(request),
        workflow_name=request.workflow_name,
        status="failed",
        output={},
        error=_workflow_start_error_payload(adapter_profile, exc),
    )


def product_workflow_run_from_row(row: WorkflowRunRow) -> ProductWorkflowRun:
    audit_event_id = row.get("audit_event_id")
    audit_id = audit_event_id if isinstance(audit_event_id, str) and audit_event_id else None
    return {
        "workflowRunId": row["id"],
        "workflowName": row["workflow_name"],
        "workflowProfile": row["workflow_profile"],
        "status": row["status"],
        "idempotencyKey": row["idempotency_key"],
        "foundryRunId": audit_id,
        "operationPath": f"/api/operations/runs/workflow/{row['id']}",
        "output": dict(row["output"]),
        "error": dict(row["error"]) if row["error"] is not None else None,
        "auditEventId": audit_id,
    }


def _connector_sync_input(
    ctx: RequestContext,
    dataset_ref: str,
    connector_name: str,
    resource_name: str,
    idempotency_key: str,
    sync_name: str | None,
    config_fingerprint: str | None,
    transaction_type: str,
) -> Mapping[str, object]:
    payload = {
        "workflowKind": "connector_sync",
        "workflowDefinitionVersion": CONNECTOR_SYNC_WORKFLOW_DEFINITION_VERSION,
        "datasetRef": dataset_ref,
        "connectorName": connector_name,
        "resourceName": resource_name,
        "syncName": sync_name,
        "configFingerprint": config_fingerprint,
        "syncRunId": connector_sync_run_id(
            tenant_id=ctx.tenant_id,
            idempotency_key=idempotency_key,
            dataset_ref=dataset_ref,
            connector_name=connector_name,
            resource_name=resource_name,
            sync_name=sync_name,
            config_fingerprint=config_fingerprint,
            transaction_type=transaction_type,
        ),
    }
    if transaction_type != "SNAPSHOT":
        payload["transactionType"] = transaction_type
    return payload


def _workflow_start_error_payload(adapter_profile: str, exc: Exception) -> Mapping[str, object]:
    if isinstance(exc, AdapterError):
        return scrub_error_mapping(exc.failure.to_payload())
    return {
        "adapterProfile": adapter_profile,
        "operation": "start_workflow",
        "kind": "unknown",
        "retryable": False,
        "operatorMessage": "workflow adapter start failed",
        "details": {"errorType": exc.__class__.__name__},
    }


def _ensure_cancellable_status_after_adapter_cancel(row: WorkflowRunRow, run: WorkflowRun) -> None:
    if run.status in {"queued", "running"}:
        raise ConflictDetected(
            "workflow adapter did not reach cancelled state",
            details={"workflow_run_id": row["id"], "adapterStatus": run.status},
        )


def _require_matching_cancelled_run(row: WorkflowRunRow, run: WorkflowRun) -> None:
    if run.run_id != row["id"] or run.workflow_name != row["workflow_name"]:
        raise ConflictDetected(
            "workflow adapter returned a different workflow run",
            details={
                "workflow_run_id": row["id"],
                "adapterRunId": run.run_id,
                "workflowName": row["workflow_name"],
                "adapterWorkflowName": run.workflow_name,
            },
        )
    if run.status in {"succeeded", "failed"}:
        raise ConflictDetected(
            "workflow adapter reports a terminal non-cancelled run",
            details={"workflow_run_id": row["id"], "adapterStatus": run.status},
        )


def _cancelled_output(
    row: WorkflowRunRow,
    reason: str | None,
    source: str,
    *,
    cleanup_state: str,
) -> Mapping[str, object]:
    return {
        **dict(row["input"]),
        "cancellation": _cancellation_payload(reason, source, cleanup_state=cleanup_state),
    }


def _cancellation_payload(reason: str | None, source: str, *, cleanup_state: str) -> Mapping[str, object]:
    return {
        "reason": reason or "operator_cancelled",
        "source": source,
        "cleanup": {
            "datasetStaging": cleanup_state,
            "committedVersionId": None,
        },
    }
