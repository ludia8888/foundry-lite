"""Worker-side execution for one DB-fenced Pipeline Graph v2 node."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineExecutionRepository,
    PipelineNodeAttemptRow,
    PipelineNodeRunRow,
    PipelineRunArtifactRow,
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
from foundry_lite.application.services.pipeline_distributed_node_support import (
    AttemptHeartbeat as _AttemptHeartbeat,
)
from foundry_lite.application.services.pipeline_distributed_node_support import (
    has_stable_idempotency as _has_stable_idempotency,
)
from foundry_lite.application.services.pipeline_distributed_node_support import (
    integer as _integer,
)
from foundry_lite.application.services.pipeline_distributed_node_support import (
    mapping_rows as _mapping_rows,
)
from foundry_lite.application.services.pipeline_distributed_node_support import (
    node_id as _node_id,
)
from foundry_lite.application.services.pipeline_distributed_node_support import (
    node_policy as _node_policy,
)
from foundry_lite.application.services.pipeline_distributed_node_support import (
    required_runtime_node as _required_runtime_node,
)
from foundry_lite.application.services.pipeline_distributed_node_support import (
    required_text as _required_text,
)
from foundry_lite.application.services.pipeline_distributed_node_support import (
    run_result as _run_result,
)
from foundry_lite.application.services.pipeline_distributed_node_support import (
    worker_context as _worker_context,
)
from foundry_lite.application.services.pipeline_distributed_node_support import (
    worker_lease as _worker_lease,
)
from foundry_lite.application.services.pipeline_graph_v2_evidence_composition import (
    build_pipeline_graph_v2_candidates,
    build_pipeline_graph_v2_error_payload,
)
from foundry_lite.application.services.pipeline_graph_v2_execution_bindings import (
    PipelineGraphV2ExecutionBindings,
)
from foundry_lite.application.services.pipeline_graph_v2_execution_evidence import (
    PipelineGraphV2ExecutionEvidenceWriter,
)
from foundry_lite.application.services.pipeline_graph_v2_execution_service import (
    PipelineGraphV2ExecutionService,
)
from foundry_lite.application.services.pipeline_graph_v2_run_completion import (
    graph_v2_terminal_state,
)
from foundry_lite.application.services.pipeline_graph_v2_runtime_composition import (
    build_pipeline_graph_v2_dispatcher,
)
from foundry_lite.application.services.pipeline_graph_v2_runtime_executor import (
    PipelineGraphV2RuntimeExecutor,
)
from foundry_lite.application.services.pipeline_graph_v2_runtime_plan import (
    PipelineGraphV2RuntimePlan,
    pipeline_graph_v2_runtime_plan,
)
from foundry_lite.application.services.pipeline_retry_policy import pipeline_retry_decision
from foundry_lite.application.services.pipeline_run_recovery import (
    PipelineExecutionLeaseGuard,
    new_pipeline_execution_lease,
)
from foundry_lite.application.services.pipeline_run_requests import (
    required_pipeline_run,
    required_pipeline_version,
)
from foundry_lite.application.services.pipeline_run_service_evidence import audit_pipeline_run
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RunResult,
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeNode,
    pipeline_runtime_artifact_from_payload,
    pipeline_runtime_artifact_payload,
)
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.application.state_transitions import PIPELINE_RUN_CANCELLED
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation

JsonObject = dict[str, object]
_TERMINAL_STATUSES = frozenset({"succeeded", "partial", "failed", "cancelled"})


class PipelineNodeRetryableFailure(RuntimeError):
    """Ask Temporal to redeliver one safely retryable node activity."""


class PipelineDistributedNodeService(CoreService):
    """Execute only the node assigned to the current Temporal activity."""

    required_dependencies = (
        "engine",
        "pipeline_repository",
        "pipeline_execution_repository",
    )
    required_collaborators = ("pipeline_graph_v2_execution_service", "runtime_service")
    engine: TransactionManager
    pipeline_repository: PipelineRepository
    pipeline_execution_repository: PipelineExecutionRepository
    pipeline_graph_v2_execution_service: PipelineGraphV2ExecutionService
    runtime_service: RuntimeEvidenceBoundary

    def drive(self, payload: Mapping[str, object]) -> JsonObject:
        operation = str(payload.get("operation") or "")
        if operation == "begin":
            return self.begin(payload)
        if operation == "execute_node":
            return self.execute_node(payload)
        if operation == "cancel_node":
            return self.cancel_node(payload)
        if operation == "finalize":
            return self.finalize(payload)
        raise InvariantViolation("pipeline DAG worker operation is invalid")

    def begin(self, payload: Mapping[str, object]) -> JsonObject:
        ctx = _worker_context(payload)
        run_id = _required_text(payload, "run_id")
        lease = new_pipeline_execution_lease()
        with self.engine.begin() as transaction:
            row = required_pipeline_run(self.pipeline_repository, transaction, ctx, run_id)
            if row["status"] in _TERMINAL_STATUSES:
                return {"runId": run_id, "status": row["status"]}
            claimed = self.pipeline_repository.claim_run_execution(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                run_id=run_id,
                timeline=list(row["timeline"]),
                execution_lease_token=lease.token,
                execution_lease_expires_at=lease.expires_at,
                execution_heartbeat_at=lease.heartbeat_at,
            )
            current = claimed or required_pipeline_run(self.pipeline_repository, transaction, ctx, run_id)
            self._append_event(transaction, ctx, current, "pipeline.run.running", payload)
        return {"runId": run_id, "status": current["status"]}

    def execute_node(self, payload: Mapping[str, object]) -> JsonObject:
        ctx = _worker_context(payload)
        row, version = self._run_and_version(ctx, _required_text(payload, "run_id"))
        if row["status"] == "cancelling":
            return {"nodeId": _node_id(payload), "status": "cancelled"}
        guard = PipelineExecutionLeaseGuard(self.engine, self.pipeline_repository, ctx, row)
        plan = pipeline_graph_v2_runtime_plan(
            version["execution_plan"] or {},
            target_node_ids=tuple(str(value) for value in row["target_node_ids"] or ()),
        )
        node = _required_runtime_node(plan.nodes, _node_id(payload))
        bindings = self.pipeline_graph_v2_execution_service.execution_bindings(guard)
        upstream_artifacts, persisted = self._upstream_state(ctx, row, payload)
        heartbeat = _AttemptHeartbeat(self.engine, self.pipeline_execution_repository, ctx)
        evidence = PipelineGraphV2ExecutionEvidenceWriter(
            transaction_manager=self.engine,
            repository=self.pipeline_execution_repository,
            ctx=ctx,
            run_id=str(row["id"]),
            execution_lease_guard=guard,
            worker_lease=_worker_lease(payload),
            on_attempt_started=heartbeat.start,
        )
        try:
            result = self._execute_runtime_node(
                ctx,
                row,
                version,
                plan,
                node,
                bindings,
                evidence,
                upstream_artifacts,
                persisted,
            )
        finally:
            heartbeat.stop()
        outcome = self._node_outcome(ctx, row, node.node_id, result)
        self._raise_if_retryable(ctx, row, node.config, version, outcome)
        return outcome

    def cancel_node(self, payload: Mapping[str, object]) -> JsonObject:
        ctx = _worker_context(payload)
        run_id = _required_text(payload, "run_id")
        node = _node_id(payload)
        with self.engine.begin() as transaction:
            row = required_pipeline_run(self.pipeline_repository, transaction, ctx, run_id)
            if row["status"] != "cancelling":
                return {"nodeId": node, "status": "running"}
            attempt = self.pipeline_execution_repository.cancel_active_node_attempt(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                run_id=run_id,
                node_id=node,
                worker_id=_required_text(payload, "workerIdentity"),
                external_execution_id=_required_text(payload, "externalExecutionId"),
                completed_at=_now(),
            )
            if attempt is not None:
                self._append_cancelled_node_event(transaction, ctx, row, node, attempt)
        return {"nodeId": node, "status": "cancelled"}

    def _append_cancelled_node_event(
        self,
        transaction: object,
        ctx: RequestContext,
        row: PipelineRunRow,
        node_id: str,
        attempt: PipelineNodeAttemptRow,
    ) -> None:
        self._append_event(
            transaction,
            ctx,
            row,
            "pipeline.node.cancelled",
            {
                "nodeId": node_id,
                "attemptNumber": attempt["attempt_number"],
                "workerId": attempt["lease_owner"],
                "fencingToken": attempt["fencing_token"],
            },
        )

    def _execute_runtime_node(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
        version: PipelineVersionRow,
        plan: PipelineGraphV2RuntimePlan,
        node: PipelineV2RuntimeNode,
        bindings: PipelineGraphV2ExecutionBindings,
        evidence: PipelineGraphV2ExecutionEvidenceWriter,
        upstream_artifacts: Mapping[str, PipelineV2RuntimeArtifact],
        persisted: Mapping[str, PipelineRunArtifactRow],
    ) -> PipelineV2RunResult:
        dispatcher = build_pipeline_graph_v2_dispatcher(
            bindings,
            ctx,
            run_id=str(row["id"]),
            pipeline_id=str(row["pipeline_id"]),
            deployment_id=self._deployment_id(ctx, version),
            plan=plan,
        )
        return PipelineGraphV2RuntimeExecutor(
            run_id=str(row["id"]),
            nodes=(node,),
            edges=plan.edges,
            dispatcher=dispatcher,
            evidence=evidence,
            candidates=build_pipeline_graph_v2_candidates(
                bindings,
                ctx,
                run_id=str(row["id"]),
                execution_plan=version["execution_plan"] or {},
            ),
            error_payload=build_pipeline_graph_v2_error_payload(bindings, ctx, str(row["id"])),
            initial_artifacts=upstream_artifacts,
            initial_persisted=persisted,
        ).execute()

    def finalize(self, payload: Mapping[str, object]) -> JsonObject:
        ctx = _worker_context(payload)
        row, version = self._run_and_version(ctx, _required_text(payload, "run_id"))
        if row["status"] in _TERMINAL_STATUSES:
            return {"runId": row["id"], "status": row["status"], "outputs": row["outputs"]}
        outcomes = _mapping_rows(payload.get("nodeOutcomes"))
        if row["status"] == "cancelling":
            return self._finalize_cancelled(ctx, row)
        result = _run_result(outcomes)
        terminal = graph_v2_terminal_state(result)
        guard = PipelineExecutionLeaseGuard(self.engine, self.pipeline_repository, ctx, row)
        with self.engine.begin() as transaction:
            guard.require_active(transaction)
            updated = self.pipeline_repository.update_run_terminal(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                run_id=str(row["id"]),
                transition=terminal.transition,
                output_dataset_ref=terminal.output_dataset_ref,
                output_version_id=terminal.output_version_id,
                outputs=list(terminal.outputs),
                timeline=list(row["timeline"]),
                error=terminal.error,
                completed_at=_now(),
            )
            if updated is None:
                raise ConflictDetected("pipeline terminal state changed concurrently")
            self._append_event(transaction, ctx, updated, terminal.event, {})
            audit_pipeline_run(
                self.runtime_service,
                transaction,
                ctx,
                terminal.status,
                "pipeline_run",
                str(row["id"]),
                {"version_id": version["id"], "outputs": list(terminal.outputs)},
            )
        return {"runId": row["id"], "status": terminal.status, "outputs": list(terminal.outputs)}

    def _run_and_version(
        self,
        ctx: RequestContext,
        run_id: str,
    ) -> tuple[PipelineRunRow, PipelineVersionRow]:
        with self.engine.begin() as transaction:
            row = required_pipeline_run(self.pipeline_repository, transaction, ctx, run_id)
            version = required_pipeline_version(
                self.pipeline_repository,
                transaction,
                ctx,
                str(row["version_id"]),
            )
        return row, version

    def _deployment_id(self, ctx: RequestContext, version: PipelineVersionRow) -> str:
        with self.engine.begin() as transaction:
            deployment = self.pipeline_execution_repository.promoted_deployment_for_version(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                pipeline_id=str(version["pipeline_id"]),
                version_id=str(version["id"]),
                plan_fingerprint=str(version["plan_fingerprint"]),
            )
        if deployment is None:
            raise InvariantViolation("pipeline deployment evidence is missing")
        return str(deployment["id"])

    def _upstream_state(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
        payload: Mapping[str, object],
    ) -> tuple[dict[str, PipelineV2RuntimeArtifact], dict[str, PipelineRunArtifactRow]]:
        outcomes = _mapping_rows(payload.get("upstreamOutcomes"))
        artifacts = {
            str(outcome["nodeId"]): pipeline_runtime_artifact_from_payload(runtime)
            for outcome in outcomes
            if isinstance((runtime := outcome.get("runtimeArtifact")), Mapping)
        }
        with self.engine.begin() as transaction:
            rows = self.pipeline_execution_repository.artifacts_for_run(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                run_id=str(row["id"]),
            )
        persisted = {str(item["node_id"]): item for item in rows if str(item["node_id"]) in artifacts}
        return artifacts, persisted

    def _node_outcome(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
        node_id: str,
        result: PipelineV2RunResult,
    ) -> JsonObject:
        runtime = next((item for item in result.runtime_artifacts if item.node_id == node_id), None)
        status = "succeeded" if runtime is not None and result.error is None else "failed"
        outcome: JsonObject = {
            "nodeId": node_id,
            "status": status,
            "outputs": list(result.outputs),
            "error": dict(result.error) if result.error is not None else None,
        }
        if runtime is not None:
            outcome["runtimeArtifact"] = pipeline_runtime_artifact_payload(runtime)
        self._append_node_event(ctx, row, outcome)
        return outcome

    def _append_node_event(self, ctx: RequestContext, row: PipelineRunRow, outcome: JsonObject) -> None:
        with self.engine.begin() as transaction:
            self._append_event(
                transaction,
                ctx,
                row,
                f"pipeline.node.{outcome['status']}",
                outcome,
            )

    def _raise_if_retryable(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
        node_config: Mapping[str, object],
        version: PipelineVersionRow,
        outcome: JsonObject,
    ) -> None:
        error = outcome.get("error")
        if outcome["status"] != "failed" or not isinstance(error, Mapping):
            return
        node_run, attempt = self._latest_attempt(ctx, row, str(outcome["nodeId"]))
        policy = _node_policy(version, str(outcome["nodeId"]))
        decision = pipeline_retry_decision(
            error,
            attempt_number=int(attempt["attempt_number"]),
            maximum_attempts=_integer(policy.get("maximumAttempts"), 3),
            requires_stable_idempotency=bool(policy.get("requiresStableIdempotency")),
            has_stable_idempotency=_has_stable_idempotency(node_config),
        )
        if not decision.is_retryable or decision.retry_at is None:
            return
        with self.engine.begin() as transaction:
            scheduled = self.pipeline_execution_repository.schedule_node_retry(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                node_run_id=str(node_run["id"]),
                attempt_id=str(attempt["id"]),
                fencing_token=int(attempt["fencing_token"]),
                retry_at=decision.retry_at,
                error_kind=decision.error_kind,
            )
        if scheduled:
            raise PipelineNodeRetryableFailure(f"pipeline node retry scheduled for {decision.retry_at}")

    def _latest_attempt(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
        node_id: str,
    ) -> tuple[PipelineNodeRunRow, PipelineNodeAttemptRow]:
        with self.engine.begin() as transaction:
            node_run = self.pipeline_execution_repository.node_run_by_run_node(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                run_id=str(row["id"]),
                node_id=node_id,
            )
            if node_run is None:
                raise InvariantViolation("pipeline node run evidence is missing")
            attempts = self.pipeline_execution_repository.attempts_for_node_run(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                node_run_id=str(node_run["id"]),
            )
        if not attempts:
            raise InvariantViolation("pipeline node attempt evidence is missing")
        return node_run, attempts[-1]

    def _finalize_cancelled(self, ctx: RequestContext, row: PipelineRunRow) -> JsonObject:
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
                completed_at=_now(),
            )
            if updated is not None:
                self._append_event(transaction, ctx, updated, "pipeline.run.cancelled", {})
        return {"runId": row["id"], "status": "cancelled", "outputs": row["outputs"]}

    def _append_event(
        self,
        transaction: object,
        ctx: RequestContext,
        row: PipelineRunRow,
        event_type: str,
        payload: Mapping[str, object],
    ) -> None:
        self.pipeline_execution_repository.append_run_event(
            transaction=transaction,
            record=PipelineRunEventRecord(
                event_id=_new_id("pevent"),
                tenant_id=ctx.tenant_id,
                run_id=str(row["id"]),
                event_type=event_type,
                payload=dict(payload),
                created_at=_now(),
                node_id=str(payload["nodeId"]) if isinstance(payload.get("nodeId"), str) else None,
            ),
        )
