"""Durable recovery and terminal observation for Pipeline control workers."""

from __future__ import annotations

from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineExecutionRepository,
)
from foundry_lite.application.ports.pipeline_repository import (
    PipelineRepository,
    PipelineRunRow,
    PipelineScheduleRow,
)
from foundry_lite.application.ports.transaction_context import (
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.pipeline_async_run_service import (
    PipelineAsyncRunService,
)
from foundry_lite.application.services.pipeline_scheduler_evidence import (
    record_pipeline_schedule_event,
)
from foundry_lite.application.services.pipeline_scheduler_results import (
    terminal_observation_values,
)
from foundry_lite.application.services.runtime_evidence_boundary import (
    RuntimeEvidenceBoundary,
)
from foundry_lite.domain.context import DEMO_ADMIN_ROLES, RequestContext


class PipelineControlWorkerService(CoreService):
    """Recover dispatch/cancellation and observe schedule terminal state."""

    required_dependencies = (
        "engine",
        "pipeline_repository",
        "pipeline_execution_repository",
    )
    required_collaborators = ("pipeline_async_run_service", "runtime_service")
    engine: TransactionManager
    pipeline_repository: PipelineRepository
    pipeline_execution_repository: PipelineExecutionRepository
    pipeline_async_run_service: PipelineAsyncRunService
    runtime_service: RuntimeEvidenceBoundary

    def tick(self, *, limit: int = 100) -> dict[str, object]:
        return {
            "dispatches": self.pipeline_async_run_service.recover_dispatches(limit=limit),
            "cancellations": self.recover_cancellations(limit=limit),
            "scheduleTerminals": self.observe_schedule_terminals(limit=limit),
        }

    def recover_cancellations(self, *, limit: int = 100) -> int:
        with self.engine.begin() as transaction:
            rows = self.pipeline_repository.cancelling_runs(
                transaction=transaction,
                limit=max(1, min(limit, 500)),
            )
        return sum(self._finish_cancelled(row) for row in rows)

    def _finish_cancelled(self, row: PipelineRunRow) -> int:
        ctx = _control_context(row)
        with self.engine.begin() as transaction:
            active = self.pipeline_execution_repository.active_node_run_count(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                run_id=str(row["id"]),
            )
        if active:
            return 0
        self.pipeline_async_run_service._complete_cancelled(ctx, row)
        return 1

    def observe_schedule_terminals(self, *, limit: int = 100) -> int:
        with self.engine.begin() as transaction:
            rows = self.pipeline_repository.unobserved_terminal_schedule_runs(
                transaction=transaction,
                limit=max(1, min(limit, 500)),
            )
        return sum(self._observe_schedule_terminal(row) for row in rows)

    def _observe_schedule_terminal(self, row: PipelineRunRow) -> int:
        ctx = _control_context(row)
        observed_at = _now()
        with self.engine.begin() as transaction:
            claimed = self.pipeline_repository.claim_terminal_schedule_observation(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                run_id=str(row["id"]),
                observed_at=observed_at,
            )
            if claimed is None:
                return 0
            schedule = self._locked_schedule(transaction, claimed)
            if schedule is None:
                return 1
            values = terminal_observation_values(schedule, claimed, observed_at)
            updated = self.pipeline_repository.update_schedule_terminal_observation(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                schedule_id=str(schedule["id"]),
                values=values,
            )
            self._record_observation(transaction, ctx, updated, claimed)
        return 1

    def _locked_schedule(
        self,
        transaction: TransactionContext,
        row: PipelineRunRow,
    ) -> PipelineScheduleRow | None:
        schedule_id = row["schedule_id"]
        if schedule_id is None:
            return None
        return self.pipeline_repository.schedule_by_id_for_update(
            transaction=transaction,
            tenant_id=str(row["tenant_id"]),
            schedule_id=schedule_id,
        )

    def _record_observation(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        schedule: PipelineScheduleRow | None,
        row: PipelineRunRow,
    ) -> None:
        if schedule is None:
            return
        record_pipeline_schedule_event(
            self.runtime_service,
            transaction,
            ctx,
            schedule,
            "run_terminal_observed",
            {"runId": row["id"], "status": row["status"], "slotStart": row["schedule_slot_at"]},
            f"pipeline-schedule-terminal:{row['id']}",
        )


def _control_context(row: PipelineRunRow) -> RequestContext:
    return RequestContext(
        tenant_id=str(row["tenant_id"]),
        actor_user_id="pipeline-control-worker",
        request_id=f"pipeline-control:{row['id']}",
        roles=DEMO_ADMIN_ROLES,
    )
