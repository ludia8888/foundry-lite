"""Durable recovery and terminal observation for Pipeline control workers."""

from __future__ import annotations

from typing import cast

from foundry_lite.application.ports.metadata_repository import MetadataRepository
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
from foundry_lite.application.services.pipeline_run_service import PipelineRunService
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
from foundry_lite.security.tenant_context import tenant_context


class PipelineControlWorkerService(CoreService):
    """Recover dispatch/cancellation/stale executions and observe schedule terminal state.

    Every scan targets tenant-scoped tables under ``FORCE ROW LEVEL SECURITY``, so
    the worker enumerates tenants (``metadata_repository.list_tenant_ids``) and binds
    ``tenant_context`` per tenant before each scan and its follow-up writes. Without
    the ambient tenant, a production non-superuser DB role sees zero rows and the
    worker silently recovers nothing (mirrors ``recoverable_pipeline_previews``).
    """

    required_dependencies = (
        "engine",
        "metadata_repository",
        "pipeline_repository",
        "pipeline_execution_repository",
    )
    required_collaborators = (
        "pipeline_async_run_service",
        "pipeline_run_service",
        "runtime_service",
    )
    engine: TransactionManager
    metadata_repository: MetadataRepository
    pipeline_repository: PipelineRepository
    pipeline_execution_repository: PipelineExecutionRepository
    pipeline_async_run_service: PipelineAsyncRunService
    pipeline_run_service: PipelineRunService
    runtime_service: RuntimeEvidenceBoundary

    def tick(self, *, limit: int = 100) -> dict[str, int]:
        totals = {"dispatches": 0, "cancellations": 0, "scheduleTerminals": 0, "staleExecutions": 0}
        for tenant_id in self.metadata_repository.list_tenant_ids():
            with tenant_context(tenant_id):
                dispatched = self.pipeline_async_run_service.recover_dispatches(tenant_id=tenant_id, limit=limit)
                totals["dispatches"] += cast(int, dispatched["recovered"])
                totals["cancellations"] += self.recover_cancellations(tenant_id=tenant_id, limit=limit)
                totals["scheduleTerminals"] += self.observe_schedule_terminals(tenant_id=tenant_id, limit=limit)
                totals["staleExecutions"] += self.pipeline_run_service.recover_stale_executions(
                    tenant_id=tenant_id, limit=limit
                )
        return totals

    def recover_cancellations(self, *, tenant_id: str, limit: int = 100) -> int:
        with self.engine.begin() as transaction:
            rows = self.pipeline_repository.cancelling_runs(
                transaction=transaction,
                tenant_id=tenant_id,
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

    def observe_schedule_terminals(self, *, tenant_id: str, limit: int = 100) -> int:
        with self.engine.begin() as transaction:
            rows = self.pipeline_repository.unobserved_terminal_schedule_runs(
                transaction=transaction,
                tenant_id=tenant_id,
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
