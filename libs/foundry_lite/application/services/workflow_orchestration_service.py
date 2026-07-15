"""Application service helpers for workflow orchestration service workflows."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import (
    AdapterError,
    ProductWorkflowRun,
    RuntimeRepository,
    WorkflowRun,
    WorkflowRunRow,
    WorkflowStartRequest,
    workflow_run_id,
)
from foundry_lite.application.ports.transaction_context import WORKFLOW_RUN_STARTING, TransactionContext
from foundry_lite.application.ports.workflow_adapter import WorkflowAdapter
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.protocols import DatasetRegistryLookup
from foundry_lite.application.services.runtime_error_payloads import scrub_error_mapping
from foundry_lite.application.services.runtime_service import RuntimeService
from foundry_lite.application.services.workflow_audit_payloads import (
    workflow_cancelled_audit_record,
    workflow_reconciliation_audit_record,
    workflow_start_audit_record,
)
from foundry_lite.application.services.workflow_orchestration_helpers import (
    CONNECTOR_SYNC_WORKFLOW_NAME as _CONNECTOR_SYNC_WORKFLOW_NAME,
)
from foundry_lite.application.services.workflow_orchestration_helpers import (
    MEDIA_PROCESSING_WORKFLOW_NAME as _MEDIA_PROCESSING_WORKFLOW_NAME,
)
from foundry_lite.application.services.workflow_orchestration_helpers import (
    cancelled_adapter_run,
    cancelled_ledger_run,
    connector_sync_request,
    ensure_cancellable,
    media_processing_request,
    product_workflow_run_from_row,
    require_same_workflow_request,
    workflow_record,
    workflow_run_from_start_exception,
    workflow_start_request_from_row,
    workflow_transition_for_run,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound

CONNECTOR_SYNC_WORKFLOW_NAME = _CONNECTOR_SYNC_WORKFLOW_NAME
MEDIA_PROCESSING_WORKFLOW_NAME = _MEDIA_PROCESSING_WORKFLOW_NAME


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
        source_name: str | None = None,
        config_fingerprint: str | None = None,
        transaction_type: str = "SNAPSHOT",
    ) -> ProductWorkflowRun:
        ctx = ctx or RequestContext()
        self._require_workflow_start(ctx, dataset_ref)
        dataset = self.dataset_registry_service.get_dataset(dataset_ref, ctx=ctx)
        request = connector_sync_request(
            ctx,
            dataset_ref,
            connector_name,
            resource_name,
            idempotency_key,
            sync_name,
            source_name,
            config_fingerprint,
            transaction_type,
        )
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
        request = media_processing_request(ctx, media_item_version_id, processor_spec, idempotency_key)
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
        return product_workflow_run_from_row(row)

    def cancel_product_workflow(
        self,
        workflow_run_id: str,
        *,
        reason: str | None = None,
        ctx: RequestContext | None = None,
    ) -> ProductWorkflowRun:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "operations:retry", "product_workflow", workflow_run_id)
        row = self._workflow_row_by_id(ctx, workflow_run_id)
        ensure_cancellable(row)
        if row["status"] == "cancelled":
            return product_workflow_run_from_row(row)
        run = self._cancel_workflow_run(ctx, row, reason=reason)
        updated = self._record_adapter_result(ctx, run)
        audited = self._audit_workflow_cancellation(ctx, updated, previous_status=row["status"], reason=reason)
        return product_workflow_run_from_row(audited)

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
                record=workflow_record(ctx, request, self.workflow_adapter.profile_name, dataset_id),
            )
            row = existing or self._workflow_row(conn, ctx, workflow_run_id(request))
        require_same_workflow_request(row, request)
        return row

    def _start_or_replay(
        self,
        ctx: RequestContext,
        request: WorkflowStartRequest,
        row: WorkflowRunRow,
    ) -> ProductWorkflowRun:
        if row["status"] not in {"requested", "start_unknown"}:
            return product_workflow_run_from_row(row)
        if row["status"] == "start_unknown":
            reconciled = self._reconcile_start_unknown_workflow(ctx, row)
            if reconciled["status"] != "start_unknown":
                return product_workflow_run_from_row(reconciled)
        claimed = self._claim_start(ctx, row["id"])
        if claimed is None:
            return product_workflow_run_from_row(self._workflow_row_by_id(ctx, row["id"]))
        run = self._start_workflow_with_evidence(workflow_start_request_from_row(row, request))
        updated = self._record_adapter_result(ctx, run)
        audited = self._audit_workflow_start(ctx, updated)
        return product_workflow_run_from_row(audited)

    def _start_workflow_with_evidence(self, request: WorkflowStartRequest) -> WorkflowRun:
        try:
            return self.workflow_adapter.start_workflow(request)
        except Exception as exc:
            return workflow_run_from_start_exception(request, self.workflow_adapter.profile_name, exc)

    def _cancel_workflow_run(self, ctx: RequestContext, row: WorkflowRunRow, *, reason: str | None) -> WorkflowRun:
        if row["status"] == "requested":
            return cancelled_ledger_run(row, reason, source="foundry_ledger")
        try:
            run = self.workflow_adapter.cancel_workflow(ctx.tenant_id, row["id"], reason=reason)
        except AdapterError as exc:
            raise ConflictDetected(
                "workflow adapter could not confirm cancellation",
                details={"workflow_run_id": row["id"], "adapterFailure": scrub_error_mapping(exc.failure.to_payload())},
            ) from exc
        if run is None:
            raise ConflictDetected(
                "workflow adapter has no cancellable run",
                details={"workflow_run_id": row["id"], "status": row["status"]},
            )
        return cancelled_adapter_run(row, run, reason)

    def _reconcile_start_unknown_workflow(self, ctx: RequestContext, row: WorkflowRunRow) -> WorkflowRunRow:
        try:
            run = self.workflow_adapter.workflow_run(ctx.tenant_id, row["id"])
        except AdapterError:
            return row
        if run is None or run.status in {"queued", "running"}:
            return row
        updated = self._record_adapter_result(ctx, run)
        return self._audit_workflow_reconciliation(ctx, updated, previous_status=row["status"])

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
        transition = workflow_transition_for_run(run)
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
                record=workflow_start_audit_record(ctx, row, audit_id=audit_id),
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

    def _audit_workflow_cancellation(
        self,
        ctx: RequestContext,
        row: WorkflowRunRow,
        *,
        previous_status: str,
        reason: str | None,
    ) -> WorkflowRunRow:
        with self.engine.begin() as conn:
            audit_id = _new_id("audit")
            self.runtime_repository.insert_audit_event(
                transaction=conn,
                record=workflow_cancelled_audit_record(ctx, row, audit_id, previous_status, reason),
            )
            self.runtime_service._run_relation(
                conn,
                ctx,
                source_run_type="workflow",
                source_run_id=row["id"],
                target_run_type="audit",
                target_run_id=audit_id,
                relation="cancelled_by",
                resource_type="product_workflow",
                resource_id=row["id"],
                metadata={"previousStatus": previous_status, "status": row["status"]},
            )
        return self._workflow_row_by_id(ctx, row["id"])

    def _audit_workflow_reconciliation(
        self,
        ctx: RequestContext,
        row: WorkflowRunRow,
        *,
        previous_status: str,
    ) -> WorkflowRunRow:
        with self.engine.begin() as conn:
            audit_id = _new_id("audit")
            self.runtime_repository.insert_audit_event(
                transaction=conn,
                record=workflow_reconciliation_audit_record(ctx, row, audit_id, previous_status),
            )
            self.runtime_service._run_relation(
                conn,
                ctx,
                source_run_type="workflow",
                source_run_id=row["id"],
                target_run_type="audit",
                target_run_id=audit_id,
                relation="reconciled_by",
                resource_type="product_workflow",
                resource_id=row["id"],
                metadata={"previousStatus": previous_status, "status": row["status"]},
            )
        return self._workflow_row_by_id(ctx, row["id"])

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
