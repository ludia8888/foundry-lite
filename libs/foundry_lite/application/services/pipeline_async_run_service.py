"""Asynchronous Pipeline DAG enqueue, dispatch, observation, and cancellation."""

from __future__ import annotations

import time
from collections.abc import Mapping

from foundry_lite.application.ports.pipeline_dag_orchestrator import (
    PipelineDagDispatchRequest,
    PipelineDagOrchestrator,
)
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineExecutionRepository,
    PipelineNodeRunRecord,
    PipelineRunEventRecord,
)
from foundry_lite.application.ports.pipeline_repository import (
    PipelineRepository,
    PipelineRunRow,
    PipelineVersionRow,
)
from foundry_lite.application.ports.transaction_context import TransactionManager
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.pipeline_async_run_events import (
    append_legacy_terminal_event,
)
from foundry_lite.application.services.pipeline_async_run_payloads import (
    decode_run_cursor,
    event_payload,
    next_run_cursor,
)
from foundry_lite.application.services.pipeline_execution_plan_backfill import (
    ensure_pipeline_execution_plan,
)
from foundry_lite.application.services.pipeline_run_recovery import PipelineExecutionLeaseLost
from foundry_lite.application.services.pipeline_run_requests import (
    cancel_request_fingerprint,
    deployed_pipeline_version,
    new_queued_run_record,
    require_idempotent_cancel,
    require_idempotent_run,
    required_pipeline_run,
    required_pipeline_version,
    run_request_fingerprint,
)
from foundry_lite.application.services.pipeline_run_service import PipelineRunService
from foundry_lite.application.services.pipeline_run_service_evidence import (
    audit_pipeline_cancelled,
    audit_pipeline_queued,
    audit_pipeline_run,
    require_pipeline_write_open,
)
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.application.state_transitions import PIPELINE_RUN_CANCELLED
from foundry_lite.domain.context import DEMO_ADMIN_ROLES, RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

JsonObject = dict[str, object]
_TERMINAL_RUN_STATUSES = frozenset({"succeeded", "partial", "failed", "cancelled"})


class PipelineAsyncRunService(CoreService):
    """Keep API requests off the node execution path."""

    required_dependencies = (
        "engine",
        "policy",
        "pipeline_repository",
        "pipeline_execution_repository",
        "pipeline_dag_orchestrator",
    )
    required_collaborators = ("pipeline_run_service", "runtime_service")
    engine: TransactionManager
    pipeline_repository: PipelineRepository
    pipeline_execution_repository: PipelineExecutionRepository
    pipeline_dag_orchestrator: PipelineDagOrchestrator
    pipeline_run_service: PipelineRunService
    runtime_service: RuntimeEvidenceBoundary

    def enqueue(
        self,
        pipeline_id: str,
        *,
        version_id: str | None,
        idempotency_key: str,
        parameters: Mapping[str, object] | None,
        target_node_ids: list[str] | None,
        wait_seconds: int,
        ctx: RequestContext,
    ) -> JsonObject:
        self._validate_wait(wait_seconds)
        self.runtime_service._require_or_audit(ctx, "pipeline:run", "pipeline", pipeline_id)
        require_pipeline_write_open(self.runtime_service, ctx, "enqueue_pipeline_run", pipeline_id)
        version = self._deployed_version(ctx, pipeline_id, version_id)
        fingerprint = run_request_fingerprint(pipeline_id, version, parameters, target_node_ids)
        row, is_created = self._create_run(
            ctx,
            pipeline_id=pipeline_id,
            version=version,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            parameters=parameters,
            target_node_ids=target_node_ids,
        )
        if not is_created:
            require_idempotent_run(row, fingerprint)
        self._dispatch(ctx, row, version)
        return self._wait_for_snapshot(ctx, str(row["id"]), wait_seconds)

    def list_runs(
        self,
        pipeline_id: str,
        *,
        cursor: str | None,
        limit: int,
        ctx: RequestContext,
    ) -> JsonObject:
        self.policy.require(ctx, "pipeline:read")
        bounded_limit = max(1, min(limit, 100))
        after_started_at, after_run_id = decode_run_cursor(cursor)
        with self.engine.begin() as transaction:
            rows = self.pipeline_repository.list_runs(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                pipeline_id=pipeline_id,
                after_started_at=after_started_at,
                after_run_id=after_run_id,
                limit=bounded_limit + 1,
            )
        page = rows[:bounded_limit]
        return {
            "items": [self.pipeline_run_service.get_run(str(row["id"]), ctx=ctx) for row in page],
            "nextCursor": next_run_cursor(rows, bounded_limit),
        }

    def events(
        self,
        run_id: str,
        *,
        after_sequence: int,
        limit: int,
        ctx: RequestContext,
    ) -> JsonObject:
        self.policy.require(ctx, "pipeline:read")
        with self.engine.begin() as transaction:
            required_pipeline_run(self.pipeline_repository, transaction, ctx, run_id)
            rows = self.pipeline_execution_repository.run_events(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                run_id=run_id,
                after_sequence=max(0, after_sequence),
                limit=max(1, min(limit, 500)),
            )
        return {"runId": run_id, "events": [event_payload(row) for row in rows]}

    def cancel(
        self,
        run_id: str,
        *,
        idempotency_key: str,
        reason: str | None,
        ctx: RequestContext,
    ) -> JsonObject:
        if not idempotency_key.strip():
            raise ValidationFailed("Idempotency-Key is required")
        self.runtime_service._require_or_audit(ctx, "pipeline:run", "pipeline_run", run_id)
        row = self._request_cancel(ctx, run_id, reason, idempotency_key)
        if str(row["status"]) in _TERMINAL_RUN_STATUSES:
            return self.pipeline_run_service.get_run(run_id, ctx=ctx)
        workflow_run_id = row["workflow_run_id"]
        if isinstance(workflow_run_id, str):
            self.pipeline_dag_orchestrator.cancel(ctx.tenant_id, workflow_run_id, reason=reason)
        return self.pipeline_run_service.get_run(run_id, ctx=ctx)

    def execute_dispatched(self, request: PipelineDagDispatchRequest) -> None:
        ctx = RequestContext(
            tenant_id=request.tenant_id,
            actor_user_id="pipeline-worker",
            request_id=request.request_id,
            roles=DEMO_ADMIN_ROLES,
        )
        with self.engine.begin() as transaction:
            row = required_pipeline_run(self.pipeline_repository, transaction, ctx, request.run_id)
            version = required_pipeline_version(
                self.pipeline_repository,
                transaction,
                ctx,
                str(row["version_id"]),
            )
        if row["status"] == "cancelling":
            self._complete_cancelled(ctx, row)
            return
        try:
            self.pipeline_run_service.execute_queued_run(ctx, row, version)
            append_legacy_terminal_event(
                self.engine,
                self.pipeline_repository,
                self.pipeline_execution_repository,
                ctx,
                request.run_id,
            )
        except PipelineExecutionLeaseLost:
            current = self._run_row(ctx, request.run_id)
            if current["status"] == "cancelling":
                self._complete_cancelled(ctx, current)
                return
            raise

    def recover_dispatches(self, *, limit: int = 100) -> JsonObject:
        with self.engine.begin() as transaction:
            rows = self.pipeline_repository.pending_dispatch_runs(
                transaction=transaction,
                tenant_id=None,
                limit=max(1, min(limit, 500)),
            )
        recovered = 0
        for row in rows:
            ctx = self._worker_context(row)
            version = self._version_for_row(ctx, row)
            self._dispatch(ctx, row, version)
            recovered += 1
        return {"recovered": recovered}

    def _deployed_version(
        self,
        ctx: RequestContext,
        pipeline_id: str,
        version_id: str | None,
    ) -> PipelineVersionRow:
        version = deployed_pipeline_version(
            self.engine,
            self.pipeline_repository,
            self.pipeline_execution_repository,
            ctx,
            pipeline_id,
            version_id,
        )
        return ensure_pipeline_execution_plan(
            self.engine,
            self.pipeline_repository,
            self.runtime_service,
            ctx,
            version,
        )

    def _create_run(
        self,
        ctx: RequestContext,
        *,
        pipeline_id: str,
        version: PipelineVersionRow,
        idempotency_key: str,
        request_fingerprint: str,
        parameters: Mapping[str, object] | None,
        target_node_ids: list[str] | None,
    ) -> tuple[PipelineRunRow, bool]:
        record = new_queued_run_record(
            ctx,
            pipeline_id=pipeline_id,
            version=version,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            parameters=parameters,
            target_node_ids=target_node_ids,
        )
        with self.engine.begin() as transaction:
            existing = self.pipeline_repository.run_by_idempotency_key(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return existing, False
            row = self.pipeline_repository.insert_run(transaction=transaction, record=record)
            if row["id"] != record.run_id:
                return row, False
            self._prepare_nodes(transaction, ctx, row, version)
            self._append_event(transaction, ctx, row, "pipeline.run.queued", {})
            audit_pipeline_queued(self.runtime_service, transaction, ctx, row)
        return row, True

    def _prepare_nodes(
        self,
        transaction: object,
        ctx: RequestContext,
        row: PipelineRunRow,
        version: PipelineVersionRow,
    ) -> None:
        plan = version["execution_plan"] or {}
        nodes = plan.get("nodes")
        if not isinstance(nodes, list):
            return
        now = _now()
        for item in nodes:
            if not isinstance(item, Mapping):
                continue
            node_id = str(item.get("nodeId") or "")
            if not node_id:
                continue
            self.pipeline_execution_repository.insert_node_run(
                transaction=transaction,
                record=PipelineNodeRunRecord(
                    node_run_id=f"{row['id']}:node:{node_id}",
                    tenant_id=ctx.tenant_id,
                    run_id=str(row["id"]),
                    node_id=node_id,
                    descriptor_id=str(item.get("descriptorId") or "unknown"),
                    spec_version=str(item.get("specVersion") or "1"),
                    input_artifacts=[],
                    created_at=now,
                ),
            )

    def _dispatch(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
        version: PipelineVersionRow,
    ) -> None:
        if row["status"] != "queued" or row["dispatch_status"] == "dispatched":
            return
        request = _dispatch_request(ctx, row, version)
        result = self.pipeline_dag_orchestrator.dispatch(request)
        dispatch_status = "dispatched" if result.status != "unknown" else "unknown"
        error: JsonObject | None = None if dispatch_status == "dispatched" else {"kind": "dispatch_unknown"}
        with self.engine.begin() as transaction:
            updated = self.pipeline_repository.update_run_dispatch(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                run_id=str(row["id"]),
                workflow_run_id=result.workflow_run_id,
                dispatch_status=dispatch_status,
                dispatch_error=error,
            )
            if updated is not None:
                self._append_event(
                    transaction,
                    ctx,
                    updated,
                    f"pipeline.run.dispatch_{dispatch_status}",
                    {"workflowRunId": result.workflow_run_id, "taskQueue": result.task_queue},
                )

    def _request_cancel(
        self,
        ctx: RequestContext,
        run_id: str,
        reason: str | None,
        idempotency_key: str,
    ) -> PipelineRunRow:
        fingerprint = cancel_request_fingerprint(run_id, reason)
        with self.engine.begin() as transaction:
            row = required_pipeline_run(self.pipeline_repository, transaction, ctx, run_id)
            require_idempotent_cancel(row, idempotency_key, fingerprint)
            if row["status"] in _TERMINAL_RUN_STATUSES or row["status"] == "cancelling":
                return row
            updated = self.pipeline_repository.request_run_cancel(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                run_id=run_id,
                requested_at=_now(),
                reason=reason,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
            )
            if updated is None:
                raise ConflictDetected("pipeline run cancellation changed concurrently")
            self._record_cancellation(transaction, ctx, updated, reason)
            return updated

    def _record_cancellation(
        self,
        transaction: object,
        ctx: RequestContext,
        row: PipelineRunRow,
        reason: str | None,
    ) -> None:
        self.pipeline_execution_repository.cancel_inactive_node_runs(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            run_id=str(row["id"]),
            completed_at=_now(),
        )
        self._append_event(
            transaction,
            ctx,
            row,
            "pipeline.run.cancelling",
            {"reason": reason or "operator_cancelled"},
        )
        audit_pipeline_run(
            self.runtime_service,
            transaction,
            ctx,
            "cancelling",
            "pipeline_run",
            str(row["id"]),
            {"reason": reason},
        )

    def _complete_cancelled(self, ctx: RequestContext, row: PipelineRunRow) -> None:
        now = _now()
        with self.engine.begin() as transaction:
            updated = self.pipeline_repository.update_run_terminal(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                run_id=str(row["id"]),
                transition=PIPELINE_RUN_CANCELLED,
                output_dataset_ref=row["output_dataset_ref"],
                output_version_id=row["output_version_id"],
                outputs=list(row["outputs"]),
                timeline=list(row["timeline"]),
                error=None,
                completed_at=now,
            )
            if updated is not None:
                self._append_event(transaction, ctx, updated, "pipeline.run.cancelled", {})
                audit_pipeline_cancelled(self.runtime_service, transaction, ctx, row)

    def _append_event(
        self,
        transaction: object,
        ctx: RequestContext,
        row: PipelineRunRow,
        event_type: str,
        payload: JsonObject,
    ) -> None:
        self.pipeline_execution_repository.append_run_event(
            transaction=transaction,
            record=PipelineRunEventRecord(
                event_id=_new_id("pevent"),
                tenant_id=ctx.tenant_id,
                run_id=str(row["id"]),
                event_type=event_type,
                payload=payload,
                created_at=_now(),
            ),
        )

    def _wait_for_snapshot(self, ctx: RequestContext, run_id: str, wait_seconds: int) -> JsonObject:
        deadline = time.monotonic() + wait_seconds
        while True:
            snapshot = self.pipeline_run_service.get_run(run_id, ctx=ctx)
            if snapshot["status"] in _TERMINAL_RUN_STATUSES or time.monotonic() >= deadline:
                return snapshot
            time.sleep(0.1)

    def _version_for_row(self, ctx: RequestContext, row: PipelineRunRow) -> PipelineVersionRow:
        with self.engine.begin() as transaction:
            return required_pipeline_version(
                self.pipeline_repository,
                transaction,
                ctx,
                str(row["version_id"]),
            )

    def _run_row(self, ctx: RequestContext, run_id: str) -> PipelineRunRow:
        with self.engine.begin() as transaction:
            return required_pipeline_run(self.pipeline_repository, transaction, ctx, run_id)

    @staticmethod
    def _worker_context(row: PipelineRunRow) -> RequestContext:
        return RequestContext(
            tenant_id=str(row["tenant_id"]),
            actor_user_id="pipeline-control-worker",
            request_id=f"dispatch-recovery:{row['id']}",
            roles=DEMO_ADMIN_ROLES,
        )

    @staticmethod
    def _validate_wait(wait_seconds: int) -> None:
        if wait_seconds < 0 or wait_seconds > 30:
            raise ValidationFailed("waitSeconds must be between 0 and 30")


def _dispatch_request(
    ctx: RequestContext,
    row: PipelineRunRow,
    version: PipelineVersionRow,
) -> PipelineDagDispatchRequest:
    return PipelineDagDispatchRequest(
        tenant_id=ctx.tenant_id,
        run_id=str(row["id"]),
        pipeline_id=str(row["pipeline_id"]),
        version_id=str(row["version_id"]),
        request_id=ctx.request_id,
        idempotency_key=str(row["idempotency_key"]),
        execution_plan=version["execution_plan"] or {},
        target_node_ids=tuple(str(value) for value in row["target_node_ids"] or ()),
    )
