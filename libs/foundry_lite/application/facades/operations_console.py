from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from foundry_lite.application.core_retry import retry_materialization_name, retry_materialization_result
from foundry_lite.application.ports import (
    BackupRestoreModeReport,
    BackupRestorePreflightReport,
    DeadLetterRecordBulkRetryResult,
    DeadLetterRecordDiscardResult,
    DeadLetterRecordRetryResult,
    DeadLetterRecordRow,
    LineageEdgeRow,
    ObservabilityDetectorConfig,
    ObservabilityReport,
    ProductWorkflowRun,
    RuntimeRetryResult,
    RuntimeRunDetail,
    RuntimeRunQueryResult,
    RuntimeRunSnapshot,
)
from foundry_lite.application.ports.iceberg_maintenance import IcebergMaintenancePlan
from foundry_lite.application.services.iceberg_maintenance_service import (
    DEFAULT_FILE_COUNT_THRESHOLD,
    DEFAULT_READ_AMPLIFICATION_THRESHOLD,
    DEFAULT_RETENTION_MIN_SNAPSHOTS,
    DEFAULT_SMALL_FILE_THRESHOLD_BYTES,
    IcebergMaintenanceService,
)
from foundry_lite.application.services.materialization_service import MaterializationService
from foundry_lite.application.services.record_dlq_service import RecordDlqService
from foundry_lite.application.services.runtime_service import RuntimeService
from foundry_lite.application.services.workflow_orchestration_service import WorkflowOrchestrationService
from foundry_lite.domain.context import RequestContext
from foundry_lite.observability.tracing import trace_public_methods


class ActionWritebackReconciler(Protocol):
    def reconcile_action_writeback(
        self,
        writeback_id: str,
        *,
        remote_status: str | None = None,
        remote_resource_id: str | None = None,
        external_writeback_uri: str | None = None,
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object]: ...


class BackupRestoreReporter(Protocol):
    def restore_preflight_report(
        self,
        *,
        ctx: RequestContext | None = None,
        backup_id: str | None = None,
    ) -> BackupRestorePreflightReport: ...

    def start_restore_mode(
        self,
        *,
        ctx: RequestContext | None = None,
        backup_id: str | None = None,
        restore_id: str | None = None,
    ) -> BackupRestoreModeReport: ...

    def restore_mode_status(
        self,
        restore_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> BackupRestoreModeReport: ...

    def approve_restore_resume(
        self,
        restore_id: str,
        *,
        ctx: RequestContext | None = None,
        validation_id: str | None = None,
    ) -> BackupRestoreModeReport: ...


@trace_public_methods
class OperationsConsole:
    """Operations bounded context: run inspection, lineage, retries, and DLQ reprocessing."""

    def __init__(
        self,
        action: ActionWritebackReconciler,
        runtime: RuntimeService,
        materialization: MaterializationService,
        record_dlq: RecordDlqService,
        backup_restore: BackupRestoreReporter,
        iceberg_maintenance: IcebergMaintenanceService,
        workflow: WorkflowOrchestrationService,
    ) -> None:
        self._action = action
        self._runtime = runtime
        self._materialization = materialization
        self._record_dlq = record_dlq
        self._backup_restore = backup_restore
        self._iceberg_maintenance = iceberg_maintenance
        self._workflow = workflow

    def lineage(self, resource_id: str, *, ctx: RequestContext | None = None) -> list[LineageEdgeRow]:
        resolved_ctx = ctx or RequestContext()
        self._runtime._require_or_audit(resolved_ctx, "operations:read:detail", "lineage", resource_id)
        return self._runtime.lineage_for_resource(resource_id, ctx=resolved_ctx)

    def list_runs(self, *, ctx: RequestContext | None = None) -> RuntimeRunSnapshot:
        return self._runtime.list_runs(ctx=ctx)

    def record_failure_audit(
        self,
        *,
        ctx: RequestContext | None = None,
        event_type: str,
        resource_type: str,
        resource_id: str | None,
        action: str,
        exc: Exception,
        decision: str = "deny",
        before_ref: Mapping[str, object] | None = None,
        after_ref: Mapping[str, object] | None = None,
        adapter: str | None = None,
    ) -> None:
        resolved_ctx = ctx or RequestContext()
        failure_after_ref = dict(after_ref or {})
        failure_after_ref["error"] = self._runtime._error_payload(exc, resolved_ctx, adapter=adapter)
        with self._runtime.engine.begin() as conn:
            self._runtime._audit(
                conn,
                resolved_ctx,
                event_type=event_type,
                resource_type=resource_type,
                resource_id=resource_id,
                action=action,
                decision=decision,
                before_ref=before_ref,
                after_ref=failure_after_ref,
            )

    def query_runs(
        self,
        *,
        ctx: RequestContext | None = None,
        run_type: str | None = None,
        status: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> RuntimeRunQueryResult:
        return self._runtime.query_runs(
            ctx=ctx,
            run_type=run_type,
            status=status,
            since=since,
            until=until,
            limit=limit,
            cursor=cursor,
        )

    def observability_report(
        self,
        *,
        ctx: RequestContext | None = None,
        configs: list[ObservabilityDetectorConfig] | None = None,
        previous_incidents: Sequence[Mapping[str, object]] | None = None,
        observed_at: str | None = None,
    ) -> ObservabilityReport:
        return self._runtime.observability_report(
            ctx=ctx,
            configs=configs or [],
            previous_incidents=previous_incidents or [],
            observed_at=observed_at,
        )

    def run_detail(self, run_type: str, run_id: str, *, ctx: RequestContext | None = None) -> RuntimeRunDetail:
        return self._runtime.run_detail(run_type, run_id, ctx=ctx)

    def restore_preflight_report(
        self,
        *,
        ctx: RequestContext | None = None,
        backup_id: str | None = None,
    ) -> BackupRestorePreflightReport:
        return self._backup_restore.restore_preflight_report(ctx=ctx, backup_id=backup_id)

    def start_restore_mode(
        self,
        *,
        ctx: RequestContext | None = None,
        backup_id: str | None = None,
        restore_id: str | None = None,
    ) -> BackupRestoreModeReport:
        return self._backup_restore.start_restore_mode(ctx=ctx, backup_id=backup_id, restore_id=restore_id)

    def restore_mode_status(
        self,
        restore_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> BackupRestoreModeReport:
        return self._backup_restore.restore_mode_status(restore_id, ctx=ctx)

    def approve_restore_resume(
        self,
        restore_id: str,
        *,
        ctx: RequestContext | None = None,
        validation_id: str | None = None,
    ) -> BackupRestoreModeReport:
        return self._backup_restore.approve_restore_resume(restore_id, ctx=ctx, validation_id=validation_id)

    def list_dead_letter_records(
        self,
        *,
        ctx: RequestContext | None = None,
        status: str | None = None,
    ) -> list[DeadLetterRecordRow]:
        return self._record_dlq.list_dead_letter_records(ctx=ctx, status=status)

    def dead_letter_record(self, record_id: str, *, ctx: RequestContext | None = None) -> DeadLetterRecordRow:
        return self._record_dlq.dead_letter_record(record_id, ctx=ctx)

    def retry_dead_letter_record(
        self,
        record_id: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> DeadLetterRecordRetryResult:
        return self._record_dlq.retry_dead_letter_record(record_id, idempotency_key=idempotency_key, ctx=ctx)

    def bulk_retry_dead_letter_records(
        self,
        record_ids: list[str],
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> DeadLetterRecordBulkRetryResult:
        return self._record_dlq.bulk_retry_dead_letter_records(
            record_ids,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def discard_dead_letter_record(
        self,
        record_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> DeadLetterRecordDiscardResult:
        return self._record_dlq.discard_dead_letter_record(record_id, ctx=ctx)

    def plan_iceberg_maintenance(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        branch: str = "main",
        small_file_threshold_bytes: int | None = None,
        file_count_threshold: int | None = None,
        read_amplification_threshold: float | None = None,
        retention_min_snapshots: int | None = None,
    ) -> IcebergMaintenancePlan:
        return self._iceberg_maintenance.plan_iceberg_maintenance(
            dataset_ref,
            ctx=ctx,
            branch=branch,
            small_file_threshold_bytes=(
                small_file_threshold_bytes
                if small_file_threshold_bytes is not None
                else DEFAULT_SMALL_FILE_THRESHOLD_BYTES
            ),
            file_count_threshold=(
                file_count_threshold if file_count_threshold is not None else DEFAULT_FILE_COUNT_THRESHOLD
            ),
            read_amplification_threshold=(
                read_amplification_threshold
                if read_amplification_threshold is not None
                else DEFAULT_READ_AMPLIFICATION_THRESHOLD
            ),
            retention_min_snapshots=(
                retention_min_snapshots if retention_min_snapshots is not None else DEFAULT_RETENTION_MIN_SNAPSHOTS
            ),
        )

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
        return self._workflow.start_connector_sync_workflow(
            dataset_ref,
            connector_name=connector_name,
            resource_name=resource_name,
            idempotency_key=idempotency_key,
            ctx=ctx,
            sync_name=sync_name,
        )

    def start_media_processing_workflow(
        self,
        *,
        media_item_version_id: str,
        processor_spec: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> ProductWorkflowRun:
        return self._workflow.start_media_processing_workflow(
            media_item_version_id=media_item_version_id,
            processor_spec=processor_spec,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def product_workflow_run(
        self,
        workflow_run_id: str,
        *,
        ctx: RequestContext | None = None,
    ) -> ProductWorkflowRun:
        return self._workflow.product_workflow_run(workflow_run_id, ctx=ctx)

    def reconcile_action_writeback(
        self,
        writeback_id: str,
        *,
        remote_status: str | None = None,
        remote_resource_id: str | None = None,
        external_writeback_uri: str | None = None,
        ctx: RequestContext | None = None,
    ) -> Mapping[str, object]:
        return self._action.reconcile_action_writeback(
            writeback_id,
            remote_status=remote_status,
            remote_resource_id=remote_resource_id,
            external_writeback_uri=external_writeback_uri,
            ctx=ctx,
        )

    def retry_dead_letter_event(self, event_id: str, *, ctx: RequestContext | None = None) -> RuntimeRetryResult:
        ctx = ctx or RequestContext()
        plan = self._runtime.dead_letter_event_retry_plan(event_id, ctx=ctx)
        materialization_name = retry_materialization_name(plan)
        materialization_result = None
        if materialization_name is not None:
            commit = self._materialization.materialize(materialization_name, ctx=ctx)
            materialization_result = retry_materialization_result(materialization_name, commit)
        result = self._runtime.retry_dead_letter_event(event_id, ctx=ctx)
        if materialization_result is not None:
            result["materializationResult"] = materialization_result
        return result
