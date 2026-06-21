from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import AuditEventRecord, ProductWorkflowRun, RuntimeRepository, RuntimeRow
from foundry_lite.application.ports.workflow_adapter import WorkflowAdapter, WorkflowRun, WorkflowStartRequest
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.dataset.protocols import DatasetRegistryLookup
from foundry_lite.application.services.runtime_service import RuntimeService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound

CONNECTOR_SYNC_WORKFLOW_NAME = "ConnectorSyncWorkflow"


class WorkflowOrchestrationService(CoreService):
    """Product workflow orchestration through the vendor-neutral workflow adapter."""

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
        self.runtime_service._require_or_audit(ctx, "dataset:write", "dataset", dataset_ref)
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation="start_connector_sync_workflow",
            resource_type="dataset",
            resource_id=dataset_ref,
        )
        dataset = self.dataset_registry_service.get_dataset(dataset_ref, ctx=ctx)
        request = WorkflowStartRequest(
            workflow_name=CONNECTOR_SYNC_WORKFLOW_NAME,
            tenant_id=ctx.tenant_id,
            request_id=ctx.request_id,
            idempotency_key=idempotency_key,
            input=_connector_sync_input(dataset_ref, connector_name, resource_name, sync_name),
        )
        run = self.workflow_adapter.start_workflow(request)
        audit_id = self._audit_event_id_for_workflow(ctx, run.run_id)
        if audit_id is None:
            audit_id = self._audit_workflow_start(
                ctx,
                run,
                dataset_id=str(dataset["id"]),
                idempotency_key=idempotency_key,
            )
        return _product_workflow_run(run, self.workflow_adapter.profile_name, idempotency_key, audit_id)

    def product_workflow_run(self, workflow_run_id: str, *, ctx: RequestContext | None = None) -> ProductWorkflowRun:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "operations:read:detail", "product_workflow", workflow_run_id)
        run = self.workflow_adapter.workflow_run(workflow_run_id)
        if run is None:
            raise NotFound("workflow run not found", details={"workflow_run_id": workflow_run_id})
        audit = self._audit_event_for_workflow(ctx, workflow_run_id)
        audit_id = str(audit["id"]) if audit is not None else None
        idempotency_key = _workflow_idempotency_key(workflow_run_id, audit)
        return _product_workflow_run(run, self.workflow_adapter.profile_name, idempotency_key, audit_id)

    def _audit_workflow_start(
        self,
        ctx: RequestContext,
        run: WorkflowRun,
        *,
        dataset_id: str,
        idempotency_key: str,
    ) -> str:
        audit_id = _new_id("audit")
        with self.engine.begin() as conn:
            self.runtime_repository.insert_audit_event(
                transaction=conn,
                record=_workflow_audit_record(
                    ctx,
                    run,
                    audit_id=audit_id,
                    dataset_id=dataset_id,
                    idempotency_key=idempotency_key,
                    workflow_profile=self.workflow_adapter.profile_name,
                ),
            )
        return audit_id

    def _audit_event_id_for_workflow(self, ctx: RequestContext, workflow_run_id: str) -> str | None:
        audit = self._audit_event_for_workflow(ctx, workflow_run_id)
        return str(audit["id"]) if audit is not None else None

    def _audit_event_for_workflow(self, ctx: RequestContext, workflow_run_id: str) -> RuntimeRow | None:
        audit_rows = self.runtime_repository.list_runs(tenant_id=ctx.tenant_id)["auditEvents"]
        for row in audit_rows:
            if row.get("resource_id") == workflow_run_id:
                return row
        return None


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


def _product_workflow_run(
    run: WorkflowRun,
    workflow_profile: str,
    idempotency_key: str,
    audit_event_id: str | None,
) -> ProductWorkflowRun:
    operation_path = f"/api/operations/runs/audit/{audit_event_id}" if audit_event_id else None
    return {
        "workflowRunId": run.run_id,
        "workflowName": run.workflow_name,
        "workflowProfile": workflow_profile,
        "status": run.status,
        "idempotencyKey": idempotency_key,
        "foundryRunId": audit_event_id,
        "operationPath": operation_path,
        "output": dict(run.output),
        "error": dict(run.error) if run.error is not None else None,
        "auditEventId": audit_event_id,
    }


def _workflow_idempotency_key(workflow_run_id: str, audit: RuntimeRow | None) -> str:
    if audit is None:
        return workflow_run_id
    after_ref = audit.get("after_ref")
    if not isinstance(after_ref, Mapping):
        return workflow_run_id
    idempotency_key = after_ref.get("idempotencyKey")
    if isinstance(idempotency_key, str) and idempotency_key:
        return idempotency_key
    return workflow_run_id


def _workflow_audit_record(
    ctx: RequestContext,
    run: WorkflowRun,
    *,
    audit_id: str,
    dataset_id: str,
    idempotency_key: str,
    workflow_profile: str,
) -> AuditEventRecord:
    return AuditEventRecord(
        event_id=audit_id,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.actor_user_id,
        event_type=f"workflow.{run.status}",
        resource_type="product_workflow",
        resource_id=run.run_id,
        action="workflow:start",
        decision="allow",
        policy_decision={"permission": "dataset:write"},
        before_ref={},
        after_ref=_workflow_after_ref(run, audit_id, dataset_id, idempotency_key, workflow_profile),
        correlation_id=run.run_id,
        request_id=ctx.request_id,
        metadata={},
        created_at=_now(),
    )


def _workflow_after_ref(
    run: WorkflowRun,
    audit_id: str,
    dataset_id: str,
    idempotency_key: str,
    workflow_profile: str,
) -> Mapping[str, object]:
    return {
        "workflowRunId": run.run_id,
        "workflowName": run.workflow_name,
        "workflowProfile": workflow_profile,
        "foundryRunId": audit_id,
        "datasetId": dataset_id,
        "idempotencyKey": idempotency_key,
        "status": run.status,
        "output": dict(run.output),
        "error": dict(run.error) if run.error is not None else None,
    }
