"""Pipeline Builder deploy, run, schedule, and timeline workflows."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.media_repository import MediaRepository
from foundry_lite.application.primitives import _now
from foundry_lite.application.services import pipeline_run_recovery, pipeline_run_unknown_commit_recovery
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.pipeline_execution_plan_backfill import (
    ensure_pipeline_execution_plan,
)
from foundry_lite.application.services.pipeline_run_component_types import (
    DatasetPipelineNodeCommitter,
    GovernedCandidatePipelineOutputCommitter,
    GovernedPipelineCandidateCommitter,
    PipelineCompilerService,
    PipelineExecutionRepository,
    PipelineGraphV2RunCoordinatorService,
    PipelineNodeCommitterRegistry,
    PipelineNodeExecutionEvidence,
    PipelineRunExecution,
    RuntimeEvidenceBoundary,
    TransformService,
    is_graph_v2_execution_plan,
    run_compiled_transforms,
    run_with_evidence_payload,
)
from foundry_lite.application.services.pipeline_run_contract_types import (
    PIPELINE_RUN_CANCELLED,
    PIPELINE_RUN_EXECUTING,
    ConflictDetected,
    DatasetRepository,
    DatasetTransactionRepository,
    DatasetVersionRepository,
    InvariantViolation,
    PipelineRepository,
    PipelineRunRow,
    PipelineVersionRow,
    RequestContext,
    TransactionContext,
)
from foundry_lite.application.services.pipeline_run_requests import (
    deployed_pipeline_version,
    new_run_record,
    require_idempotent_run,
    required_pipeline_run,
    required_pipeline_version,
    run_request_fingerprint,
)
from foundry_lite.application.services.pipeline_run_service_evidence import (
    audit_pipeline_execution_claim,
    audit_pipeline_run,
    require_pipeline_write_open,
)
from foundry_lite.application.services.pipeline_run_terminal import (
    complete_unsuccessful_pipeline_run,
    fail_pipeline_run,
    succeed_pipeline_run,
)

_ACTIVE_OR_TERMINAL_RUN_STATUSES = frozenset(
    {"cancelled", "cancelling", "running", "executing", "succeeded", "partial", "failed"}
)


def _is_active_or_terminal_run(row: PipelineRunRow) -> bool:
    return str(row["status"]) in _ACTIVE_OR_TERMINAL_RUN_STATUSES


class PipelineRunService(CoreService):
    """Deploy compiled pipeline versions and run them through existing transforms."""

    required_dependencies = (
        "engine",
        "policy",
        "pipeline_repository",
        "pipeline_execution_repository",
        "dataset_repository",
        "dataset_transaction_repository",
        "dataset_version_repository",
        "media_repository",
    )
    required_collaborators = (
        "pipeline_compiler_service",
        "pipeline_graph_v2_run_coordinator_service",
        "media_transaction_service",
        "runtime_service",
        "transform_service",
    )
    dataset_repository: DatasetRepository
    dataset_transaction_repository: DatasetTransactionRepository
    dataset_version_repository: DatasetVersionRepository
    media_repository: MediaRepository
    media_transaction_service: MediaTransactionService
    pipeline_compiler_service: PipelineCompilerService
    pipeline_graph_v2_run_coordinator_service: PipelineGraphV2RunCoordinatorService
    pipeline_execution_repository: PipelineExecutionRepository
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
        require_pipeline_write_open(self.runtime_service, ctx, "start_pipeline_run", pipeline_id)
        version = deployed_pipeline_version(
            self.engine,
            self.pipeline_repository,
            self.pipeline_execution_repository,
            ctx,
            pipeline_id,
            version_id,
        )
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
            action = pipeline_run_recovery.replayed_pipeline_run_action(row)
            if action == "fail_stale":
                self.expire_stale_execution_run(ctx, row)
                return self.get_run(str(row["id"]), ctx=ctx)
            if action == "read":
                self._reconcile_unknown_commit(ctx, row)
                return self.get_run(str(row["id"]), ctx=ctx)
        try:
            return self.execute_queued_run(ctx, row, version)
        except (
            pipeline_run_recovery.PipelineExecutionLeaseLost,
            pipeline_run_recovery.PipelineTerminalCommitError,
        ):
            raise
        except Exception as exc:
            fail_pipeline_run(self.engine, self.pipeline_repository, self.runtime_service, ctx, row, exc)
            return self.get_run(str(row["id"]), ctx=ctx)

    def _reconcile_unknown_commit(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
    ) -> None:
        if not pipeline_run_unknown_commit_recovery.has_unknown_commit_output(row):
            return
        pipeline_run_unknown_commit_recovery.reconcile_unknown_commit_outputs(
            self.engine,
            self.pipeline_repository,
            self.pipeline_execution_repository,
            self.dataset_transaction_repository,
            self.media_repository,
            self.media_transaction_service,
            self.runtime_service,
            ctx,
            row,
        )

    def expire_stale_execution_run(self, ctx: RequestContext, row: PipelineRunRow) -> bool:
        """Fail-close one run whose execution lease lapsed; return whether it did.

        Owned here rather than in the control worker because the terminal recovery
        needs this service's execution/dataset/media repositories, and the worker
        should not take those dependencies just to pass them through.
        """
        return pipeline_run_recovery.expire_stale_pipeline_run(
            self.engine,
            self.pipeline_repository,
            self.pipeline_execution_repository,
            self.dataset_transaction_repository,
            self.media_repository,
            self.runtime_service,
            ctx,
            row,
        )

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
        audit_pipeline_run(
            self.runtime_service,
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
            row = required_pipeline_run(self.pipeline_repository, conn, ctx, run_id)
            version = required_pipeline_version(self.pipeline_repository, conn, ctx, str(row["version_id"]))
            return run_with_evidence_payload(
                transaction=conn,
                repository=self.pipeline_execution_repository,
                tenant_id=ctx.tenant_id,
                row=row,
                execution_plan=version["execution_plan"] or {"nodes": []},
            )

    def get_run_timeline(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        run = self.get_run(run_id, ctx=ctx)
        return {"runId": run_id, "timeline": run["timeline"]}

    def cancel_run(self, run_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "pipeline:run", "pipeline_run", run_id)
        with self.engine.begin() as conn:
            row = required_pipeline_run(self.pipeline_repository, conn, ctx, run_id)
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
                audit_pipeline_run(
                    self.runtime_service,
                    conn,
                    ctx,
                    "cancelled",
                    "pipeline_run",
                    run_id,
                    {"version_id": row["version_id"], "outputs": list(row["outputs"])},
                )
        return self.get_run(run_id, ctx=ctx)

    def execute_queued_run(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
        version: PipelineVersionRow,
    ) -> dict[str, object]:
        claimed = self._claim_run_execution(ctx, row)
        if claimed is None:
            return self.get_run(str(row["id"]), ctx=ctx)
        with pipeline_run_recovery.pipeline_execution_heartbeat(
            self.engine,
            self.pipeline_repository,
            ctx,
            claimed,
        ) as execution_lease_guard:
            return self._execute_claimed_run(ctx, claimed, version, execution_lease_guard)

    def _execute_claimed_run(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
        version: PipelineVersionRow,
        execution_lease_guard: pipeline_run_recovery.PipelineExecutionLeaseGuard,
    ) -> dict[str, object]:
        if is_graph_v2_execution_plan(version["execution_plan"]):
            run_id = self.pipeline_graph_v2_run_coordinator_service.execute(
                ctx,
                row=row,
                version=version,
                execution_lease_guard=execution_lease_guard,
            )
            return self.get_run(run_id, ctx=ctx)
        return self._execute_legacy_run(ctx, row, version, execution_lease_guard)

    def _execute_legacy_run(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
        version: PipelineVersionRow,
        execution_lease_guard: pipeline_run_recovery.PipelineExecutionLeaseGuard,
    ) -> dict[str, object]:
        evidence = self._node_evidence(ctx, row, version, execution_lease_guard)
        compiled = self.pipeline_compiler_service.compile_graph(
            pipeline_id=str(version["pipeline_id"]),
            version_id=str(version["id"]),
            graph=version["graph"],
            ctx=ctx,
            target_node_ids=row["target_node_ids"],
        )
        timeline = [*row["timeline"], {"event": "pipeline.compile.completed", "at": _now(), **compiled}]
        committers = self._node_committers(ctx, row, version, evidence, execution_lease_guard)
        execution = run_compiled_transforms(committers, compiled, timeline)
        return self._complete_legacy_execution(
            ctx,
            row,
            version,
            compiled,
            timeline,
            execution,
            evidence,
            execution_lease_guard,
        )

    def _complete_legacy_execution(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
        version: PipelineVersionRow,
        compiled: Mapping[str, object],
        timeline: list[dict[str, object]],
        execution: PipelineRunExecution,
        evidence: PipelineNodeExecutionEvidence,
        execution_lease_guard: pipeline_run_recovery.PipelineExecutionLeaseGuard,
    ) -> dict[str, object]:
        if execution.error is not None:
            complete_unsuccessful_pipeline_run(
                self.engine,
                self.pipeline_repository,
                self.runtime_service,
                ctx,
                row,
                compiled,
                timeline,
                execution,
                evidence,
                execution_lease_guard,
            )
            return self.get_run(str(row["id"]), ctx=ctx)
        succeed_pipeline_run(
            self.engine,
            self.pipeline_repository,
            self.runtime_service,
            ctx,
            row,
            version,
            compiled,
            timeline,
            list(execution.outputs),
            execution_lease_guard,
        )
        return self.get_run(str(row["id"]), ctx=ctx)

    def _claim_run_execution(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
    ) -> PipelineRunRow | None:
        run_id = str(row["id"])
        lease = pipeline_run_recovery.new_pipeline_execution_lease()
        timeline: list[dict[str, object]] = [
            *row["timeline"],
            {"event": "pipeline.run.execution_claimed", "at": lease.heartbeat_at, "leaseExpiresAt": lease.expires_at},
        ]
        with self.engine.begin() as conn:
            claimed = self.pipeline_repository.claim_run_execution(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                run_id=run_id,
                timeline=timeline,
                execution_lease_token=lease.token,
                execution_lease_expires_at=lease.expires_at,
                execution_heartbeat_at=lease.heartbeat_at,
            )
            if claimed is not None:
                audit_pipeline_execution_claim(self.runtime_service, conn, ctx, row, lease.expires_at)
                return claimed
            current = required_pipeline_run(self.pipeline_repository, conn, ctx, run_id)
            if _is_active_or_terminal_run(current):
                return None
        raise ConflictDetected(
            "pipeline run execution claim changed concurrently",
            details={"run_id": run_id, "status": current["status"]},
        )

    def _node_evidence(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
        version: PipelineVersionRow,
        execution_lease_guard: pipeline_run_recovery.PipelineExecutionLeaseGuard,
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
            execution_lease_guard=execution_lease_guard,
        )

    def _node_committers(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
        version: PipelineVersionRow,
        evidence: PipelineNodeExecutionEvidence,
        execution_lease_guard: pipeline_run_recovery.PipelineExecutionLeaseGuard,
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
            execution_lease_guard=execution_lease_guard,
        )
        return PipelineNodeCommitterRegistry(
            dataset=DatasetPipelineNodeCommitter(self.transform_service, evidence, ctx),
            governed_candidate=GovernedCandidatePipelineOutputCommitter(candidate),
        )
