"""Operations console routes: runs, observability, backup/restore, maintenance, DLQ."""

from __future__ import annotations

from typing import Protocol, cast

from fastapi import APIRouter, Header, Query, Request
from foundry_lite.application.action_types import (
    ActionWritebackQueueResult,
    ActionWritebackReconciliationResult,
    ActionWritebackRecoveryItem,
)
from foundry_lite.application.admin_overview import AdminReadinessOverview, AdminTaskPlan
from foundry_lite.application.ports import (
    BackupRestoreArtifactReceipt,
    BackupRestoreArtifactRestoreReport,
    BackupRestoreModeReport,
    BackupRestorePostRestoreValidationReport,
    BackupRestorePreflightReport,
    BackupRestoreRecoveryOverview,
    DeadLetterRecordBulkRetryResult,
    DeadLetterRecordDiscardResult,
    DeadLetterRecordRetryResult,
    DeadLetterRecordRow,
    LineageEdgeRow,
    ObjectIndexRebuildResult,
    ObservabilityDetectorConfig,
    ObservabilityReport,
    ObservabilityStoredReport,
    OntologyObjectReindexResult,
    ProductWorkflowRun,
    RuntimeRetryResult,
    RuntimeRunDetail,
    RuntimeRunQueryResult,
    StoredObservabilityIncident,
    TransformRecordDlqRetryResult,
    TransformRetryResult,
)
from foundry_lite.domain.errors import FoundryLiteError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import (
    ActionWritebackReconciliationRequest,
    ActionWritebackRecoveryApprovalRequest,
    BackupRestoreArtifactCreateRequest,
    BackupRestoreArtifactRestoreRequest,
    BackupRestoreModeStartRequest,
    BackupRestorePostRestoreValidationRequest,
    BackupRestorePreflightRequest,
    BackupRestoreResumeApprovalRequest,
    ConnectorSyncWorkflowStartRequest,
    DeadLetterBulkRetryRequest,
    JsonObject,
    ObservabilityDetectRequest,
    ObservabilityResolveRequest,
    OntologyObjectReindexRequest,
    OutboxPublishRequest,
    ProductWorkflowCancelRequest,
)

router = APIRouter()


class PromptArtifactPayload(Protocol):
    @property
    def artifact_id(self) -> str: ...

    @property
    def ai_run_id(self) -> str: ...

    @property
    def content_hash(self) -> str: ...

    @property
    def export_marking(self) -> str: ...

    @property
    def plaintext(self) -> str: ...


def _prompt_artifact_payload(result: PromptArtifactPayload) -> dict[str, object]:
    return {
        "artifactId": result.artifact_id,
        "aiRunId": result.ai_run_id,
        "contentHash": result.content_hash,
        "exportMarking": result.export_marking,
        "plaintext": result.plaintext,
    }


@router.get("/api/operations/runs")
def list_operation_runs(
    request: Request,
    run_type: str | None = Query(default=None, alias="runType"),
    status: str | None = Query(default=None),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    limit: int = Query(default=50),
    cursor: str | None = Query(default=None),
) -> RuntimeRunQueryResult:
    try:
        return runtime.foundry.operations.query_runs(
            ctx=_ctx(request),
            run_type=run_type,
            status=status,
            since=since,
            until=until,
            limit=limit,
            cursor=cursor,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/operations/lineage")
def get_operation_lineage(
    request: Request,
    resource_id: str = Query(alias="resourceId"),
) -> list[LineageEdgeRow]:
    try:
        return runtime.foundry.operations.lineage(resource_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/observability/detect")
def detect_observability_incidents(request: Request, payload: ObservabilityDetectRequest) -> ObservabilityReport:
    try:
        return runtime.foundry.operations.observability_report(
            ctx=_ctx(request),
            configs=cast(list[ObservabilityDetectorConfig], payload.configs),
            previous_incidents=payload.previous_incidents,
            observed_at=payload.observed_at,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/observability/detect-and-record")
def record_observability_incidents(
    request: Request,
    payload: ObservabilityDetectRequest,
) -> ObservabilityStoredReport:
    try:
        return runtime.foundry.operations.record_observability_report(
            ctx=_ctx(request),
            configs=cast(list[ObservabilityDetectorConfig], payload.configs),
            observed_at=payload.observed_at,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/operations/observability/incidents")
def list_observability_incidents(
    request: Request,
    status: str | None = None,
    limit: int = 50,
) -> list[StoredObservabilityIncident]:
    try:
        return runtime.foundry.operations.list_observability_incidents(ctx=_ctx(request), status=status, limit=limit)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/observability/incidents/{incident_id}/acknowledge")
def acknowledge_observability_incident(request: Request, incident_id: str) -> StoredObservabilityIncident:
    try:
        return runtime.foundry.operations.acknowledge_observability_incident(incident_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/observability/incidents/{incident_id}/resolve")
def resolve_observability_incident(
    request: Request,
    incident_id: str,
    payload: ObservabilityResolveRequest,
) -> StoredObservabilityIncident:
    try:
        return runtime.foundry.operations.resolve_observability_incident(
            incident_id, ctx=_ctx(request), reason=payload.reason
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/backup-restore/preflight")
def backup_restore_preflight(
    request: Request,
    payload: BackupRestorePreflightRequest,
) -> BackupRestorePreflightReport:
    try:
        return runtime.foundry.operations.restore_preflight_report(ctx=_ctx(request), backup_id=payload.backup_id)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/backup-restore/artifacts")
def create_backup_restore_artifact(
    request: Request,
    payload: BackupRestoreArtifactCreateRequest,
) -> BackupRestoreArtifactReceipt:
    try:
        return runtime.foundry.operations.create_backup_artifact(ctx=_ctx(request), backup_id=payload.backup_id)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/backup-restore/artifacts/restore")
def restore_from_backup_restore_artifact(
    request: Request,
    payload: BackupRestoreArtifactRestoreRequest,
) -> BackupRestoreArtifactRestoreReport:
    try:
        return runtime.foundry.operations.restore_from_backup_artifact(
            ctx=_ctx(request),
            artifact_ref=payload.artifact_ref,
            artifact_hash=payload.artifact_hash,
            restore_id=payload.restore_id,
            validation_id=payload.validation_id,
            should_run_post_restore_validation=payload.should_run_post_restore_validation,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/backup-restore/artifacts/restore/execute")
def execute_backup_restore_artifact_restore(
    request: Request,
    payload: BackupRestoreArtifactRestoreRequest,
) -> BackupRestoreArtifactRestoreReport:
    try:
        return runtime.foundry.operations.execute_backup_artifact_restore(
            ctx=_ctx(request),
            artifact_ref=payload.artifact_ref,
            artifact_hash=payload.artifact_hash,
            restore_id=payload.restore_id,
            validation_id=payload.validation_id,
            should_run_post_restore_validation=payload.should_run_post_restore_validation,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/backup-restore/restore-mode/start")
def start_backup_restore_mode(
    request: Request,
    payload: BackupRestoreModeStartRequest,
) -> BackupRestoreModeReport:
    try:
        return runtime.foundry.operations.start_restore_mode(
            ctx=_ctx(request),
            backup_id=payload.backup_id,
            restore_id=payload.restore_id,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/operations/backup-restore/restore-mode/{restore_id}")
def get_backup_restore_mode_status(request: Request, restore_id: str) -> BackupRestoreModeReport:
    try:
        return runtime.foundry.operations.restore_mode_status(restore_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/backup-restore/restore-mode/{restore_id}/post-restore-validation")
def run_backup_restore_post_restore_validation(
    request: Request,
    restore_id: str,
    payload: BackupRestorePostRestoreValidationRequest,
) -> BackupRestorePostRestoreValidationReport:
    try:
        return runtime.foundry.operations.run_post_restore_validation(
            restore_id,
            ctx=_ctx(request),
            validation_id=payload.validation_id,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/backup-restore/restore-mode/{restore_id}/approve-resume")
def approve_backup_restore_resume(
    request: Request,
    restore_id: str,
    payload: BackupRestoreResumeApprovalRequest,
) -> BackupRestoreModeReport:
    try:
        return runtime.foundry.operations.approve_restore_resume(
            restore_id,
            ctx=_ctx(request),
            validation_id=payload.validation_id,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/outbox/publish")
def publish_pending_outbox(request: Request, payload: OutboxPublishRequest) -> JsonObject:
    try:
        return cast(
            JsonObject,
            runtime.foundry.operations.publish_pending_outbox(
                ctx=_ctx(request),
                stream_name=payload.stream_name,
                limit=payload.limit,
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/operations/admin/overview")
def get_operations_admin_overview(request: Request) -> AdminReadinessOverview:
    try:
        return runtime.foundry.operations.admin_readiness_overview(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/operations/admin/task-plan")
def get_operations_admin_task_plan(request: Request) -> AdminTaskPlan:
    try:
        return runtime.foundry.operations.admin_task_plan(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/operations/recovery/overview")
def get_operations_recovery_overview(request: Request) -> BackupRestoreRecoveryOverview:
    try:
        return runtime.foundry.operations.recovery_overview(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/operations/runs/{run_type}/{run_id}")
def get_operation_run_detail(request: Request, run_type: str, run_id: str) -> RuntimeRunDetail:
    try:
        return runtime.foundry.operations.run_detail(run_type, run_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/operations/runs/ai/{run_id}/prompt-artifacts/{artifact_id}")
def get_ai_prompt_artifact(request: Request, run_id: str, artifact_id: str) -> dict[str, object]:
    try:
        result = runtime.foundry.operations.read_prompt_artifact(run_id, artifact_id, ctx=_ctx(request))
        return _prompt_artifact_payload(result)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/workflows/connector-sync/start")
def start_connector_sync_workflow(
    request: Request,
    payload: ConnectorSyncWorkflowStartRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> ProductWorkflowRun:
    try:
        return runtime.foundry.operations.start_connector_sync_workflow(
            payload.dataset_ref,
            connector_name=payload.connector_name,
            resource_name=payload.resource_name,
            sync_name=payload.sync_name,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/operations/workflows/{workflow_run_id}")
def get_product_workflow_run(request: Request, workflow_run_id: str) -> ProductWorkflowRun:
    try:
        return runtime.foundry.operations.product_workflow_run(workflow_run_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/workflows/{workflow_run_id}/cancel")
def cancel_product_workflow_run(
    request: Request,
    workflow_run_id: str,
    payload: ProductWorkflowCancelRequest,
) -> ProductWorkflowRun:
    try:
        return runtime.foundry.operations.cancel_product_workflow(
            workflow_run_id, reason=payload.reason, ctx=_ctx(request)
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/operations/reconciliation/writebacks")
def list_action_writeback_reconciliation_queue(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> ActionWritebackQueueResult:
    try:
        return runtime.foundry.operations.list_unresolved_action_writebacks(
            status=status, limit=limit, ctx=_ctx(request)
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/reconciliation/{writeback_id}/resolve")
def resolve_action_writeback_reconciliation(
    request: Request,
    writeback_id: str,
    payload: ActionWritebackReconciliationRequest,
) -> ActionWritebackReconciliationResult:
    try:
        return cast(
            ActionWritebackReconciliationResult,
            runtime.foundry.operations.reconcile_action_writeback(
                writeback_id,
                remote_status=payload.remote_status,
                remote_resource_id=payload.remote_resource_id,
                external_writeback_uri=payload.external_writeback_uri,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/reconciliation/{writeback_id}/approve-recovery")
def approve_action_writeback_recovery(
    request: Request,
    writeback_id: str,
    payload: ActionWritebackRecoveryApprovalRequest,
) -> ActionWritebackRecoveryItem:
    try:
        return cast(
            ActionWritebackRecoveryItem,
            runtime.foundry.operations.approve_action_writeback_recovery(
                writeback_id,
                approval_id=payload.approval_id,
                reason=payload.reason,
                external_writeback_uri=payload.external_writeback_uri,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/operations/maintenance/iceberg")
def get_iceberg_maintenance_plan(
    request: Request,
    dataset_ref: str = Query(alias="datasetRef"),
    branch: str = Query(default="main"),
    small_file_threshold_bytes: int | None = Query(default=None, alias="smallFileThresholdBytes"),
    file_count_threshold: int | None = Query(default=None, alias="fileCountThreshold"),
    read_amplification_threshold: float | None = Query(default=None, alias="readAmplificationThreshold"),
    retention_min_snapshots: int | None = Query(default=None, alias="retentionMinSnapshots"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            runtime.foundry.operations.plan_iceberg_maintenance(
                dataset_ref,
                ctx=_ctx(request),
                branch=branch,
                small_file_threshold_bytes=small_file_threshold_bytes,
                file_count_threshold=file_count_threshold,
                read_amplification_threshold=read_amplification_threshold,
                retention_min_snapshots=retention_min_snapshots,
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/maintenance/iceberg/{dataset_ref}/plan")
def plan_iceberg_maintenance(
    request: Request,
    dataset_ref: str,
    branch: str = Query(default="main"),
    small_file_threshold_bytes: int | None = Query(default=None, alias="smallFileThresholdBytes"),
    file_count_threshold: int | None = Query(default=None, alias="fileCountThreshold"),
    read_amplification_threshold: float | None = Query(default=None, alias="readAmplificationThreshold"),
    retention_min_snapshots: int | None = Query(default=None, alias="retentionMinSnapshots"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            runtime.foundry.operations.plan_iceberg_maintenance(
                dataset_ref,
                ctx=_ctx(request),
                branch=branch,
                small_file_threshold_bytes=small_file_threshold_bytes,
                file_count_threshold=file_count_threshold,
                read_amplification_threshold=read_amplification_threshold,
                retention_min_snapshots=retention_min_snapshots,
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/maintenance/iceberg/{dataset_ref}/run")
def run_iceberg_maintenance(
    request: Request,
    dataset_ref: str,
    branch: str = Query(default="main"),
    small_file_threshold_bytes: int | None = Query(default=None, alias="smallFileThresholdBytes"),
    file_count_threshold: int | None = Query(default=None, alias="fileCountThreshold"),
    read_amplification_threshold: float | None = Query(default=None, alias="readAmplificationThreshold"),
    retention_min_snapshots: int | None = Query(default=None, alias="retentionMinSnapshots"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            runtime.foundry.operations.run_iceberg_maintenance(
                dataset_ref,
                ctx=_ctx(request),
                branch=branch,
                small_file_threshold_bytes=small_file_threshold_bytes,
                file_count_threshold=file_count_threshold,
                read_amplification_threshold=read_amplification_threshold,
                retention_min_snapshots=retention_min_snapshots,
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/operations/dead-letter-records")
def list_dead_letter_records(
    request: Request,
    status: str | None = Query(default=None),
) -> list[DeadLetterRecordRow]:
    try:
        return runtime.foundry.operations.list_dead_letter_records(ctx=_ctx(request), status=status)
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/dead-letter-records/bulk-retry")
def bulk_retry_dead_letter_records(
    request: Request,
    payload: DeadLetterBulkRetryRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> DeadLetterRecordBulkRetryResult:
    try:
        return runtime.foundry.operations.bulk_retry_dead_letter_records(
            payload.ids,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/operations/dead-letter-records/{record_id}")
def get_dead_letter_record(request: Request, record_id: str) -> DeadLetterRecordRow:
    try:
        return runtime.foundry.operations.dead_letter_record(record_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/dead-letter-records/{record_id}/retry")
def retry_dead_letter_record(
    request: Request,
    record_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> DeadLetterRecordRetryResult:
    try:
        return runtime.foundry.operations.retry_dead_letter_record(
            record_id,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/dead-letter-records/{record_id}/retry-transform")
def retry_transform_dead_letter_record(
    request: Request,
    record_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> TransformRecordDlqRetryResult:
    try:
        return runtime.foundry.transforms.retry_dead_letter_record(
            record_id,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/dead-letter-records/{record_id}/discard")
def discard_dead_letter_record(request: Request, record_id: str) -> DeadLetterRecordDiscardResult:
    try:
        return runtime.foundry.operations.discard_dead_letter_record(record_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/dead-letter-events/{event_id}/retry")
def retry_dead_letter_event(request: Request, event_id: str) -> RuntimeRetryResult:
    try:
        return runtime.foundry.operations.retry_dead_letter_event(event_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/index/{object_type}/replay")
def replay_object_index(request: Request, object_type: str) -> ObjectIndexRebuildResult:
    try:
        return runtime.foundry.objects.reindex(object_type, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/index/{object_type}/ontology-reindex")
def replay_ontology_object_reindex(
    request: Request,
    object_type: str,
    payload: OntologyObjectReindexRequest,
) -> OntologyObjectReindexResult:
    try:
        return runtime.foundry.objects.reindex_ontology_migration(
            object_type,
            payload.reindex_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/runs/index/{run_id}/replay")
def replay_failed_index_run(request: Request, run_id: str) -> ObjectIndexRebuildResult:
    try:
        return runtime.foundry.objects.replay_index_run(run_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/operations/runs/transform/{run_id}/retry")
def retry_failed_transform_run(request: Request, run_id: str) -> TransformRetryResult:
    try:
        return runtime.foundry.transforms.retry_run(run_id, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
