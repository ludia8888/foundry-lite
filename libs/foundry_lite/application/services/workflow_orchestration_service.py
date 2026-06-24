from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import (
    AuditEventRecord,
    ProductWorkflowRun,
    RuntimeJsonObject,
    RuntimeRepository,
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
    WORKFLOW_RUN_STARTING,
    WORKFLOW_RUN_SUCCEEDED,
    StatusTransition,
    TransactionContext,
)
from foundry_lite.application.ports.workflow_adapter import WorkflowAdapter
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.protocols import DatasetRegistryLookup
from foundry_lite.application.services.runtime_service import RuntimeService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound

CONNECTOR_SYNC_WORKFLOW_NAME = "ConnectorSyncWorkflow"
MEDIA_PROCESSING_WORKFLOW_NAME = "MediaProcessingWorkflow"


class WorkflowOrchestrationService(CoreService):
    """Product workflow orchestration through a Foundry-owned run ledger."""

    required_dependencies = ("engine", "runtime_repository", "workflow_adapter")
    required_collaborators = ("dataset_registry_service", "runtime_service")

    runtime_repository: RuntimeRepository
    workflow_adapter: WorkflowAdapter
    dataset_registry_service: DatasetRegistryLookup
    runtime_service: RuntimeService

    def start_connector_sync_workflow(
        self,
        dataset_ref: str,
        *,
        connector_name: str,
        resource_name: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        sync_name: str | None = None,
    ) -> ProductWorkflowRun:
        ctx = ctx or RequestContext()
        self._require_workflow_start(ctx, dataset_ref)
        dataset = self.dataset_registry_service.get_dataset(dataset_ref, ctx=ctx)
        request = _connector_sync_request(ctx, dataset_ref, connector_name, resource_name, idempotency_key, sync_name)
        row = self._ensure_workflow_intent(ctx, request, dataset_id=str(dataset["id"]))
        return self._start_or_replay(ctx, request, row)

    def start_media_processing_workflow(
        self,
        *,
        media_item_version_id: str,
        processor_spec: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> ProductWorkflowRun:
        ctx = ctx or RequestContext()
        self._require_media_workflow_start(ctx, media_item_version_id)
        request = _media_processing_request(ctx, media_item_version_id, processor_spec, idempotency_key)
        row = self._ensure_workflow_intent(ctx, request, dataset_id=None)
        return self._start_or_replay(ctx, request, row)

    def product_workflow_run(self, workflow_run_id: str, *, ctx: RequestContext | None = None) -> ProductWorkflowRun:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "operations:read:detail", "product_workflow", workflow_run_id)
        with self.engine.begin() as conn:
            row = self.runtime_repository.workflow_run_by_id(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                workflow_run_id=workflow_run_id,
            )
        if row is None:
            raise NotFound("workflow run not found", details={"workflow_run_id": workflow_run_id})
        return _product_workflow_run_from_row(row)

    def _require_workflow_start(self, ctx: RequestContext, dataset_ref: str) -> None:
        self.runtime_service._require_or_audit(ctx, "dataset:write", "dataset", dataset_ref)
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="start_connector_sync_workflow",
            resource_type="dataset",
            resource_id=dataset_ref,
        )

    def _require_media_workflow_start(self, ctx: RequestContext, media_item_version_id: str) -> None:
        self.runtime_service._require_or_audit(ctx, "dataset:write", "media_item_version", media_item_version_id)
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="start_media_processing_workflow",
            resource_type="media_item_version",
            resource_id=media_item_version_id,
        )

    def _ensure_workflow_intent(
        self,
        ctx: RequestContext,
        request: WorkflowStartRequest,
        *,
        dataset_id: str | None,
    ) -> WorkflowRunRow:
        with self.engine.begin() as conn:
            existing = self.runtime_repository.insert_workflow_run_or_get_existing(
                transaction=conn,
                record=_workflow_record(ctx, request, self.workflow_adapter.profile_name, dataset_id),
            )
            row = existing or self._workflow_row(conn, ctx, workflow_run_id(request))
        _require_same_workflow_request(row, request)
        return row

    def _start_or_replay(
        self,
        ctx: RequestContext,
        request: WorkflowStartRequest,
        row: WorkflowRunRow,
    ) -> ProductWorkflowRun:
        if row["status"] not in {"requested", "start_unknown"}:
            return _product_workflow_run_from_row(row)
        claimed = self._claim_start(ctx, row["id"])
        if claimed is None:
            return _product_workflow_run_from_row(self._workflow_row_by_id(ctx, row["id"]))
        run = self.workflow_adapter.start_workflow(request)
        updated = self._record_adapter_result(ctx, run)
        audited = self._audit_workflow_start(ctx, updated)
        return _product_workflow_run_from_row(audited)

    def _claim_start(self, ctx: RequestContext, workflow_run_id_value: str) -> WorkflowRunRow | None:
        now = _now()
        with self.engine.begin() as conn:
            return self.runtime_repository.update_workflow_run_status(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                workflow_run_id=workflow_run_id_value,
                transition=WORKFLOW_RUN_STARTING,
                output={},
                error=None,
                started_at=now,
                completed_at=None,
            )

    def _record_adapter_result(self, ctx: RequestContext, run: WorkflowRun) -> WorkflowRunRow:
        transition = _workflow_transition_for_run(run)
        completed_at = _now() if run.status in {"succeeded", "failed", "cancelled"} else None
        with self.engine.begin() as conn:
            row = self.runtime_repository.update_workflow_run_status(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                workflow_run_id=run.run_id,
                transition=transition,
                output=dict(run.output),
                error=dict(run.error) if run.error is not None else None,
                started_at=None,
                completed_at=completed_at,
            )
        return row or self._workflow_row_by_id(ctx, run.run_id)

    def _audit_workflow_start(self, ctx: RequestContext, row: WorkflowRunRow) -> WorkflowRunRow:
        audit_id = row.get("audit_event_id")
        if isinstance(audit_id, str) and audit_id:
            return row
        with self.engine.begin() as conn:
            audit_id = _new_id("audit")
            self.runtime_repository.insert_audit_event(
                transaction=conn,
                record=_workflow_audit_record(ctx, row, audit_id=audit_id),
            )
            linked = self.runtime_repository.link_workflow_audit_event(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                workflow_run_id=row["id"],
                audit_event_id=audit_id,
            )
            self.runtime_service._run_relation(
                conn,
                ctx,
                source_run_type="workflow",
                source_run_id=row["id"],
                target_run_type="audit",
                target_run_id=audit_id,
                relation="audited_by",
                resource_type="product_workflow",
                resource_id=row["id"],
                metadata={"status": row["status"]},
            )
        return linked or self._workflow_row_by_id(ctx, row["id"])

    def _workflow_row_by_id(self, ctx: RequestContext, workflow_run_id_value: str) -> WorkflowRunRow:
        with self.engine.begin() as conn:
            return self._workflow_row(conn, ctx, workflow_run_id_value)

    def _workflow_row(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        workflow_run_id_value: str,
    ) -> WorkflowRunRow:
        row = self.runtime_repository.workflow_run_by_id(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            workflow_run_id=workflow_run_id_value,
        )
        if row is None:
            raise NotFound("workflow run not found", details={"workflow_run_id": workflow_run_id_value})
        return row


def _connector_sync_request(
    ctx: RequestContext,
    dataset_ref: str,
    connector_name: str,
    resource_name: str,
    idempotency_key: str,
    sync_name: str | None,
) -> WorkflowStartRequest:
    return WorkflowStartRequest(
        workflow_name=CONNECTOR_SYNC_WORKFLOW_NAME,
        tenant_id=ctx.tenant_id,
        request_id=ctx.request_id,
        idempotency_key=idempotency_key,
        input=_connector_sync_input(dataset_ref, connector_name, resource_name, sync_name),
    )


def _connector_sync_input(
    dataset_ref: str,
    connector_name: str,
    resource_name: str,
    sync_name: str | None,
) -> Mapping[str, object]:
    return {
        "workflowKind": "connector_sync",
        "datasetRef": dataset_ref,
        "connectorName": connector_name,
        "resourceName": resource_name,
        "syncName": sync_name,
    }


def _media_processing_request(
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
        input=_media_processing_input(media_item_version_id, processor_spec),
    )


def _media_processing_input(
    media_item_version_id: str,
    processor_spec: Mapping[str, object],
) -> Mapping[str, object]:
    return {
        "workflowKind": "media_processing",
        "mediaItemVersionId": media_item_version_id,
        "processorSpec": dict(processor_spec),
    }


def _workflow_record(
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


def _require_same_workflow_request(row: WorkflowRunRow, request: WorkflowStartRequest) -> None:
    if row["request_fingerprint"] == workflow_request_fingerprint(request):
        return
    raise ConflictDetected(
        "workflow idempotency key already belongs to a different request",
        details={"workflow_run_id": row["id"], "workflow_name": row["workflow_name"]},
    )


def _workflow_transition_for_run(run: WorkflowRun) -> StatusTransition:
    if run.status == "succeeded":
        return WORKFLOW_RUN_SUCCEEDED
    if run.status == "cancelled":
        return WORKFLOW_RUN_CANCELLED
    if run.status in {"queued", "running"}:
        return WORKFLOW_RUN_RUNNING
    if _is_retryable_start_unknown(run.error):
        return WORKFLOW_RUN_START_UNKNOWN
    return WORKFLOW_RUN_FAILED


def _is_retryable_start_unknown(error: Mapping[str, object] | None) -> bool:
    if error is None:
        return False
    kind = error.get("kind")
    retryable = error.get("retryable")
    return kind in {"timeout", "unavailable"} and retryable is True


def _product_workflow_run_from_row(row: WorkflowRunRow) -> ProductWorkflowRun:
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


def _workflow_audit_record(ctx: RequestContext, row: WorkflowRunRow, *, audit_id: str) -> AuditEventRecord:
    return AuditEventRecord(
        event_id=audit_id,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.actor_user_id,
        event_type=f"workflow.{row['status']}",
        resource_type="product_workflow",
        resource_id=row["id"],
        action="workflow:start",
        decision="allow",
        policy_decision={"permission": "dataset:write"},
        before_ref={},
        after_ref=_workflow_after_ref(row, audit_id),
        correlation_id=row["id"],
        request_id=ctx.request_id,
        metadata={},
        created_at=_now(),
    )


def _workflow_after_ref(row: WorkflowRunRow, audit_id: str) -> RuntimeJsonObject:
    return {
        "workflowRunId": row["id"],
        "workflowName": row["workflow_name"],
        "workflowProfile": row["workflow_profile"],
        "foundryRunId": audit_id,
        "datasetId": row["dataset_id"],
        "idempotencyKey": row["idempotency_key"],
        "status": row["status"],
        "output": dict(row["output"]),
        "error": dict(row["error"]) if row["error"] is not None else None,
    }
