"""Durable terminal-state coordination for production Graph v2 runs."""

from __future__ import annotations

from foundry_lite.application.ports import TransactionContext, TransactionManager
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineExecutionRepository,
)
from foundry_lite.application.ports.pipeline_repository import (
    PipelineRepository,
    PipelineRunRow,
    PipelineVersionRow,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.pipeline_graph_v2_execution_service import (
    PipelineGraphV2ExecutionService,
)
from foundry_lite.application.services.pipeline_graph_v2_run_completion import (
    PipelineGraphV2TerminalState,
    graph_v2_terminal_state,
)
from foundry_lite.application.services.runtime_evidence_boundary import (
    RuntimeEvidenceBoundary,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, InvariantViolation


class PipelineGraphV2RunCoordinatorService(CoreService):
    """Own run-level terminal CAS while node executors own domain commits."""

    required_dependencies = (
        "engine",
        "pipeline_repository",
        "pipeline_execution_repository",
    )
    required_collaborators = (
        "pipeline_graph_v2_execution_service",
        "runtime_service",
    )
    engine: TransactionManager
    pipeline_execution_repository: PipelineExecutionRepository
    pipeline_repository: PipelineRepository
    pipeline_graph_v2_execution_service: PipelineGraphV2ExecutionService
    runtime_service: RuntimeEvidenceBoundary

    def execute(
        self,
        ctx: RequestContext,
        *,
        row: PipelineRunRow,
        version: PipelineVersionRow,
    ) -> str:
        plan = version["execution_plan"]
        if plan is None:
            raise InvariantViolation(
                "deployed Graph v2 version is missing its execution plan",
                details={"versionId": version["id"]},
            )
        result = self.pipeline_graph_v2_execution_service.execute(
            ctx,
            run_id=str(row["id"]),
            pipeline_id=str(version["pipeline_id"]),
            deployment_id=self._deployment_id(ctx, version),
            execution_plan=plan,
            target_node_ids=row["target_node_ids"] or (),
        )
        terminal = graph_v2_terminal_state(result)
        return self._commit_terminal(ctx, row, version, terminal)

    def _deployment_id(
        self,
        ctx: RequestContext,
        version: PipelineVersionRow,
    ) -> str:
        with self.engine.begin() as transaction:
            deployments = self.pipeline_execution_repository.list_deployments(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                pipeline_id=str(version["pipeline_id"]),
                limit=100,
            )
        matching = next(
            (
                row
                for row in deployments
                if row["version_id"] == version["id"]
                and row["plan_fingerprint"] == version["plan_fingerprint"]
                and row["status"] == "PROMOTED"
            ),
            None,
        )
        if matching is None:
            raise InvariantViolation(
                "deployed Graph v2 version has no matching promoted deployment",
                details={"versionId": version["id"]},
            )
        return str(matching["id"])

    def _commit_terminal(
        self,
        ctx: RequestContext,
        row: PipelineRunRow,
        version: PipelineVersionRow,
        terminal: PipelineGraphV2TerminalState,
    ) -> str:
        with self.engine.begin() as transaction:
            after = self.pipeline_repository.update_run_terminal(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                run_id=str(row["id"]),
                transition=terminal.transition,
                output_dataset_ref=terminal.output_dataset_ref,
                output_version_id=terminal.output_version_id,
                outputs=list(terminal.outputs),
                timeline=_terminal_timeline(row, terminal),
                error=terminal.error,
                completed_at=_now(),
            )
            if after is None:
                raise ConflictDetected("pipeline Graph v2 terminal state changed concurrently")
            self._audit_terminal(transaction, ctx, row, version, terminal)
        return str(after["id"])

    def _audit_terminal(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        row: PipelineRunRow,
        version: PipelineVersionRow,
        terminal: PipelineGraphV2TerminalState,
    ) -> None:
        self.runtime_service._audit(
            transaction,
            ctx,
            event_type=f"pipeline.{terminal.status}",
            resource_type="pipeline_run",
            resource_id=str(row["id"]),
            action=terminal.status,
            after_ref={
                "versionId": version["id"],
                "outputs": list(terminal.outputs),
                "error": terminal.error,
            },
        )


def _terminal_timeline(
    row: PipelineRunRow,
    terminal: PipelineGraphV2TerminalState,
) -> list[dict[str, object]]:
    event: dict[str, object] = {
        "event": terminal.event,
        "at": _now(),
        "outputCount": len(terminal.outputs),
    }
    if terminal.error is not None:
        event["error"] = dict(terminal.error)
    return [*row["timeline"], *terminal.timeline, event]
