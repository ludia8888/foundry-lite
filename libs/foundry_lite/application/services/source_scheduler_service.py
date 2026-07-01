"""Application service helpers for source scheduler service workflows."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from foundry_lite.application.ports import RuntimeRepository, SourceManagementRepository, TransactionContext
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.source_management_service import SourceManagementService
from foundry_lite.application.services.source_scheduler_config import (
    SourceScheduleDecision,
    source_schedule_decision,
)
from foundry_lite.application.services.source_scheduler_views import scheduler_tick_view
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

_WORKFLOW_TERMINAL = {"succeeded", "failed", "cancelled"}
_MAX_RUNS_PER_TICK = 500


class SourceSchedulerService(CoreService):
    """Evaluate managed Source schedules and start due recurring sync runs."""

    required_dependencies = ("engine", "policy", "source_management_repository", "runtime_repository")
    required_collaborators = ("source_management_service",)

    source_management_repository: SourceManagementRepository
    runtime_repository: RuntimeRepository
    source_management_service: SourceManagementService

    def preview_due_managed_syncs(
        self,
        *,
        ctx: RequestContext | None = None,
        now: datetime | None = None,
        max_runs: int = 50,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:read")
        max_runs = _validated_max_runs(max_runs)
        current = _now(now)
        decisions = self._preview_decisions(ctx, current)
        return scheduler_tick_view(decisions, [], [], current, max_runs=max_runs)

    def run_due_managed_syncs(
        self,
        *,
        ctx: RequestContext | None = None,
        now: datetime | None = None,
        max_runs: int = 50,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:write")
        max_runs = _validated_max_runs(max_runs)
        current = _now(now)
        decisions = self._run_decisions(ctx, current)
        skipped = [decision.as_dict() for decision in decisions if not decision.is_due]
        started = self._start_due(ctx, decisions, max_runs=max_runs)
        return scheduler_tick_view(decisions, started, skipped, current, max_runs=max_runs)

    def _preview_decisions(self, ctx: RequestContext, now: datetime) -> list[SourceScheduleDecision]:
        decisions: list[SourceScheduleDecision] = []
        for sync in self.source_management_repository.list_syncs(tenant_id=ctx.tenant_id):
            if sync["status"] != "active":
                continue
            runs = self.source_management_repository.list_sync_runs(
                tenant_id=ctx.tenant_id,
                sync_name=str(sync["sync_name"]),
            )
            decisions.append(source_schedule_decision(sync, runs, now=now))
        return decisions

    def _run_decisions(self, ctx: RequestContext, now: datetime) -> list[SourceScheduleDecision]:
        decisions: list[SourceScheduleDecision] = []
        for sync in self.source_management_repository.list_syncs(tenant_id=ctx.tenant_id):
            if sync["status"] != "active":
                continue
            runs = self._reconciled_runs(ctx, sync)
            decisions.append(source_schedule_decision(sync, runs, now=now))
        return decisions

    def _reconciled_runs(self, ctx: RequestContext, sync: Mapping[str, object]) -> list[Mapping[str, object]]:
        rows = self.source_management_repository.list_sync_runs(
            tenant_id=ctx.tenant_id,
            sync_name=str(sync["sync_name"]),
        )
        return [self._reconcile_run(ctx, row) for row in rows]

    def _reconcile_run(self, ctx: RequestContext, run: Mapping[str, object]) -> Mapping[str, object]:
        if run["status"] != "running" or not isinstance(run.get("workflow_run_id"), str):
            return run
        workflow = self._workflow_row(ctx, str(run["workflow_run_id"]))
        if workflow is None or workflow["status"] not in _WORKFLOW_TERMINAL:
            return run
        return self._finish_workflow_backed_run(ctx, run, workflow)

    def _workflow_row(self, ctx: RequestContext, workflow_run_id: str) -> Mapping[str, object] | None:
        with self.engine.begin() as conn:
            return self.runtime_repository.workflow_run_by_id(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                workflow_run_id=workflow_run_id,
            )

    def _finish_workflow_backed_run(
        self,
        ctx: RequestContext,
        run: Mapping[str, object],
        workflow: Mapping[str, object],
    ) -> Mapping[str, object]:
        output = _mapping(workflow.get("output"))
        status = "succeeded" if workflow["status"] == "succeeded" else "failed"
        error = None if status == "succeeded" else _mapping(workflow.get("error"))
        with self.engine.begin() as conn:
            return self._update_run(conn, ctx, run, status, output, error)

    def _update_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run: Mapping[str, object],
        status: str,
        output: Mapping[str, object],
        error: Mapping[str, object] | None,
    ) -> Mapping[str, object]:
        updated = self.source_management_repository.update_sync_run_result(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            run_id=str(run["id"]),
            status=status,
            dataset_version_id=_optional_text(output.get("committedVersionId")),
            checkpoint_end={},
            result_summary={"workflowOutput": dict(output)},
            error=error,
            completed_at=_iso(datetime.now(UTC)),
        )
        return updated or run

    def _start_due(
        self, ctx: RequestContext, decisions: list[SourceScheduleDecision], *, max_runs: int
    ) -> list[dict[str, object]]:
        started: list[dict[str, object]] = []
        for decision in decisions:
            if len(started) >= max_runs or not decision.is_due or decision.idempotency_key is None:
                continue
            started.append(self._start_one(ctx, decision))
        return started

    def _start_one(self, ctx: RequestContext, decision: SourceScheduleDecision) -> dict[str, object]:
        run = self.source_management_service.start_managed_sync_run(
            decision.sync_name,
            idempotency_key=str(decision.idempotency_key),
            ctx=ctx,
            trigger_type="scheduled",
            batch_limit=decision.batch_limit,
        )
        return {"decision": decision.as_dict(), "run": run}


def _now(value: datetime | None) -> datetime:
    return (value or datetime.now(UTC)).astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _validated_max_runs(value: int) -> int:
    if value < 1 or value > _MAX_RUNS_PER_TICK:
        raise ValidationFailed("scheduler max_runs must be between 1 and 500", details={"maxRuns": value})
    return value
