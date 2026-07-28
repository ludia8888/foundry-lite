"""Pipeline Builder deploy, run, schedule, and timeline workflows."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.primitives import _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.pipeline_execution_plan_backfill import (
    ensure_pipeline_execution_plan,
)
from foundry_lite.application.services.pipeline_run_component_types import (
    DatasetPipelineNodeCommitter,
    GovernedCandidatePipelineOutputCommitter,
    GovernedPipelineCandidateCommitter,
    PipelineCompilerService,
    PipelineGraphV2RunCoordinatorService,
    PipelineNodeCommitterRegistry,
    PipelineNodeEvidenceRepository,
    PipelineNodeExecutionEvidence,
    PipelineRunExecution,
    RuntimeEvidenceBoundary,
    TransformService,
    is_graph_v2_execution_plan,
    legacy_output_fields,
    run_compiled_transforms,
    run_with_evidence_payload,
    unsuccessful_run_completion,
)
from foundry_lite.application.services.pipeline_run_contract_types import (
    PIPELINE_RUN_CANCELLED,
    PIPELINE_RUN_EXECUTING,
    PIPELINE_RUN_FAILED,
    PIPELINE_RUN_SUCCEEDED,
    ConflictDetected,
    DatasetRepository,
    DatasetVersionRepository,
    InvariantViolation,
    NotFound,
    PipelineRepository,
    PipelineRunRow,
    PipelineVersionRow,
    RequestContext,
    TransactionContext,
)
from foundry_lite.application.services.pipeline_run_recovery import (
    PipelineTerminalCommitError,
    replayed_pipeline_run_action,
    stale_pipeline_run_error,
)
from foundry_lite.application.services.pipeline_run_requests import (
    deployed_pipeline_version,
    new_run_record,
    require_deployed,
    require_idempotent_run,
    require_pipeline_match,
    run_request_fingerprint,
)

_require_deployed = require_deployed
_require_pipeline_match = require_pipeline_match


class PipelineRunService(CoreService):
    """Deploy compiled pipeline versions and run them through existing transforms."""

    required_dependencies = (
        "engine",
        "policy",
        "pipeline_repository",
        "pipeline_execution_repository",
        "dataset_repository",
        "dataset_version_repository",
    )
    required_collaborators = (
        "pipeline_compiler_service",
        "pipeline_graph_v2_run_coordinator_service",
        "runtime_service",
        "transform_service",
    )
    dataset_repository: DatasetRepository
    dataset_version_repository: DatasetVersionRepository
    pipeline_compiler_service: PipelineCompilerService
    pipeline_graph_v2_run_coordinator_service: PipelineGraphV2RunCoordinatorService
    pipeline_execution_repository: PipelineNodeEvidenceRepository
    pipeline_repository: PipelineRepository
    runtime_service: RuntimeEvidenceBoundary
    transform_service: TransformService

    def start_run(
        self,
        pipeline_id: str,
        *,
        version_id: str | None = None,
        idempotency_key: str | None = None,
        parameters: Mapping[str, object] | None = None,
        target_node_ids: list[str] | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "pipeline:run", "pipeline", pipeline_id)
        self._require_write_open(ctx, "start_pipeline_run", pipeline_id)
        version = deployed_pipeline_version(self.engine, self.pipeline_repository, ctx, pipeline_id, version_id)
        version = ensure_pipeline_execution_plan(
            self.engine, self.pipeline_repository, self.runtime_service, ctx, version
        )
        request_fingerprint = run_request_fingerprint(pipeline_id, version, parameters, target_node_ids)
        effective_key = idempotency_key or f"request:{ctx.request_id}:{pipeline_id}:{version['id']}"
        row, is_created = self._create_or_replay_run(
            ctx,
            pipeline_id=pipeline_id,
            version=version,
            idempotency_key=effective_key,
            request_fingerprint=request_fingerprint,
            parameters=parameters,
            target_node_ids=target_node_ids,
        )
        return self._execute_or_recover(ctx, row, version, is_created, request_fingerprint)

    def _execute_or_recover(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
        version: PipelineVersionRow,
        is_created: bool,
        request_fingerprint: str,
    ) -> dict[str, object]:
        if not is_created:
            require_idempotent_run(row, request_fingerprint)
            action = replayed_pipeline_run_action(row)
            if action == "fail_stale":
                return self._fail_run(ctx, row, stale_pipeline_run_error(row))
            if action == "read":
                return self.get_run(str(row["id"]), ctx=ctx)
        try:
            return self._execute_run(ctx, row, version)
        except PipelineTerminalCommitError:
            raise
        except Exception as exc:
            return self._fail_run(ctx, row, exc)

    def _create_or_replay_run(
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
        record = new_run_record(
            ctx,
            pipeline_id=pipeline_id,
            version=version,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            parameters=parameters,
            target_node_ids=target_node_ids,
        )
        with self.engine.begin() as conn:
            existing = self.pipeline_repository.run_by_idempotency_key(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return existing, False
            row = self.pipeline_repository.insert_run(
                transaction=conn,
                record=record,
            )
            if row["id"] != record.run_id:
                return row, False
            self._audit_run_started(conn, ctx, row, version)
        return row, True

    def _audit_run_started(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        row: PipelineRunRow,
        version: PipelineVersionRow,
    ) -> None:
        self._audit(
            conn,
            ctx,
            "started",
            "pipeline_run",
            str(row["id"]),
            {"pipeline_id": row["pipeline_id"], "version_id": version["id"]},
        )

    def get_run(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        with self.engine.begin() as conn:
            return self._run_payload(conn, ctx, self._require_run(conn, ctx, run_id))

    def get_run_timeline(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        run = self.get_run(run_id, ctx=ctx)
        return {"runId": run_id, "timeline": run["timeline"]}

    def cancel_run(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "pipeline:run", "pipeline_run", run_id)
        with self.engine.begin() as conn:
            row = self._require_run(conn, ctx, run_id)
            if row["status"] != "running":
                message = (
                    "pipeline run execution already started; cancellation is no longer safe"
                    if row["status"] == PIPELINE_RUN_EXECUTING.to_status
                    else "pipeline run is already terminal"
                )
                raise ConflictDetected(message, details={"run_id": run_id, "status": row["status"]})
            after = self.pipeline_repository.update_run_terminal(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                run_id=run_id,
                transition=PIPELINE_RUN_CANCELLED,
                output_dataset_ref=row["output_dataset_ref"],
                output_version_id=row["output_version_id"],
                outputs=list(row["outputs"]),
                timeline=[*row["timeline"], {"event": "pipeline.run.cancelled", "at": _now()}],
                error=None,
                completed_at=_now(),
            )
            if after is not None:
                self._audit(
                    conn,
                    ctx,
                    "cancelled",
                    "pipeline_run",
                    run_id,
                    {"version_id": row["version_id"], "outputs": list(row["outputs"])},
                )
        return self.get_run(run_id, ctx=ctx)

    def _execute_run(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
        version: PipelineVersionRow,
    ) -> dict[str, object]:
        claimed = self._claim_run_execution(ctx, row)
        if claimed is None:
            return self.get_run(str(row["id"]), ctx=ctx)
        row = claimed
        if is_graph_v2_execution_plan(version["execution_plan"]):
            run_id = self.pipeline_graph_v2_run_coordinator_service.execute(
                ctx,
                row=row,
                version=version,
            )
            return self.get_run(run_id, ctx=ctx)
        evidence = self._node_evidence(ctx, row, version)
        compiled = self.pipeline_compiler_service.compile_graph(
            pipeline_id=str(version["pipeline_id"]),
            version_id=str(version["id"]),
            graph=version["graph"],
            ctx=ctx,
            target_node_ids=row["target_node_ids"],
        )
        timeline = [*row["timeline"], {"event": "pipeline.compile.completed", "at": _now(), **compiled}]
        committers = self._node_committers(ctx, row, version, evidence)
        execution = run_compiled_transforms(committers, compiled, timeline)
        if execution.error is not None:
            return self._complete_unsuccessful_run(ctx, row, compiled, timeline, execution, evidence)
        return self._succeed_run(ctx, row, version, compiled, timeline, list(execution.outputs))

    def _claim_run_execution(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
    ) -> PipelineRunRow | None:
        run_id = str(row["id"])
        timeline: list[dict[str, object]] = [
            *row["timeline"],
            {"event": "pipeline.run.execution_claimed", "at": _now()},
        ]
        with self.engine.begin() as conn:
            claimed = self.pipeline_repository.claim_run_execution(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                run_id=run_id,
                timeline=timeline,
            )
            if claimed is not None:
                self._audit(conn, ctx, "execution_claimed", "pipeline_run", run_id, {"version_id": row["version_id"]})
                return claimed
            current = self._require_run(conn, ctx, run_id)
            if current["status"] in {"cancelled", "executing", "succeeded", "failed"}:
                return None
        raise ConflictDetected(
            "pipeline run execution claim changed concurrently",
            details={"run_id": run_id, "status": current["status"]},
        )

    def _succeed_run(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
        version: PipelineVersionRow,
        compiled: Mapping[str, object],
        timeline: list[dict[str, object]],
        outputs: list[dict[str, object]],
    ) -> dict[str, object]:
        output_dataset_ref, output_version_id = legacy_output_fields(compiled, outputs)
        with self.engine.begin() as conn:
            after = self.pipeline_repository.update_run_terminal(
                transaction=conn,
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
            if after is not None:
                self._audit(conn, ctx, "succeeded", "pipeline_run", str(row["id"]), {"version_id": version["id"]})
        return self.get_run(str(row["id"]), ctx=ctx)

    def _complete_unsuccessful_run(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
        compiled: Mapping[str, object],
        timeline: list[dict[str, object]],
        execution: PipelineRunExecution,
        evidence: PipelineNodeExecutionEvidence,
    ) -> dict[str, object]:
        state = unsuccessful_run_completion(self.runtime_service, ctx, row, compiled, timeline, execution)
        try:
            with self.engine.begin() as conn:
                evidence.fail_and_skip(
                    conn,
                    failed_attempt=execution.failed_attempt,
                    failed_item=execution.failed_item,
                    skipped_items=execution.skipped_items,
                    error=state.error,
                )
                after = self.pipeline_repository.update_run_terminal(
                    transaction=conn,
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
                self._audit(conn, ctx, state.status, "pipeline_run", str(row["id"]), {"outputs": state.outputs})
        except Exception as exc:
            raise PipelineTerminalCommitError("pipeline terminal evidence transaction failed") from exc
        return self.get_run(str(row["id"]), ctx=ctx)

    def _fail_run(self, ctx: RequestContext, row: PipelineRunRow, exc: Exception) -> dict[str, object]:
        run_id = str(row["id"])
        error = self.runtime_service._error_payload(exc, ctx, run_id=run_id)
        with self.engine.begin() as conn:
            current = self._require_run(conn, ctx, run_id)
            after = self.pipeline_repository.update_run_terminal(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                run_id=run_id,
                transition=PIPELINE_RUN_FAILED,
                output_dataset_ref=current["output_dataset_ref"],
                output_version_id=current["output_version_id"],
                outputs=list(current["outputs"]),
                timeline=[
                    *current["timeline"],
                    {"event": "pipeline.run.failed", "at": _now(), "error": dict(error)},
                ],
                error=dict(error),
                completed_at=_now(),
            )
            if after is not None:
                self._audit(conn, ctx, "failed", "pipeline_run", run_id, {"error": dict(error)})
        return self.get_run(run_id, ctx=ctx)

    def _node_evidence(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
        version: PipelineVersionRow,
    ) -> PipelineNodeExecutionEvidence:
        plan = version["execution_plan"]
        if plan is None:
            raise InvariantViolation(
                "deployed pipeline version is missing its pinned execution plan",
                details={"version_id": version["id"]},
            )
        return PipelineNodeExecutionEvidence(
            transaction_manager=self.engine,
            repository=self.pipeline_execution_repository,
            dataset_repository=self.dataset_repository,
            dataset_version_repository=self.dataset_version_repository,
            ctx=ctx,
            run_id=str(row["id"]),
            execution_plan=plan,
        )

    def _node_committers(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
        version: PipelineVersionRow,
        evidence: PipelineNodeExecutionEvidence,
    ) -> PipelineNodeCommitterRegistry:
        plan = version["execution_plan"]
        if plan is None:
            raise InvariantViolation("deployed pipeline version is missing its pinned execution plan")
        candidate = GovernedPipelineCandidateCommitter(
            transaction_manager=self.engine,
            repository=self.pipeline_execution_repository,
            dataset_repository=self.dataset_repository,
            dataset_version_repository=self.dataset_version_repository,
            runtime_service=self.runtime_service,
            ctx=ctx,
            run_id=str(row["id"]),
            execution_plan=plan,
        )
        return PipelineNodeCommitterRegistry(
            dataset=DatasetPipelineNodeCommitter(self.transform_service, evidence, ctx),
            governed_candidate=GovernedCandidatePipelineOutputCommitter(candidate),
        )

    def _run_payload(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        row: PipelineRunRow,
    ) -> dict[str, object]:
        version = self._require_version(conn, ctx, str(row["version_id"]))
        return run_with_evidence_payload(
            transaction=conn,
            repository=self.pipeline_execution_repository,
            tenant_id=ctx.tenant_id,
            row=row,
            execution_plan=version["execution_plan"] or {"nodes": []},
        )

    def _require_version(self, conn: TransactionContext, ctx: RequestContext, version_id: str) -> PipelineVersionRow:
        row = self.pipeline_repository.version_by_id(transaction=conn, tenant_id=ctx.tenant_id, version_id=version_id)
        if row is None:
            raise NotFound("pipeline version not found", details={"version_id": version_id})
        return row

    def _require_run(self, conn: TransactionContext, ctx: RequestContext, run_id: str) -> PipelineRunRow:
        row = self.pipeline_repository.run_by_id(transaction=conn, tenant_id=ctx.tenant_id, run_id=run_id)
        if row is None:
            raise NotFound("pipeline run not found", details={"run_id": run_id})
        return row

    def _require_write_open(self, ctx: RequestContext, operation: str, resource_id: str) -> None:
        self.runtime_service._require_write_traffic_open(
            ctx,
            operation=operation,
            resource_type="pipeline",
            resource_id=resource_id,
        )

    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event: str,
        resource_type: str,
        resource_id: str,
        after_ref: Mapping[str, object],
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type=f"pipeline.{event}",
            resource_type=resource_type,
            resource_id=resource_id,
            action=event,
            after_ref=after_ref,
        )
