"""Application service helpers for source scheduler service workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from foundry_lite.application.ports import (
    RuntimeRepository,
    SourceManagementRepository,
    SourceRegistryRepository,
    TransactionContext,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.source_management_config import (
    normalized_source_schedule,
    require_idempotency_key,
)
from foundry_lite.application.services.source_management_helpers import (
    SourceRuntimeBoundary,
    require_write,
    sync_row,
)
from foundry_lite.application.services.source_management_service import SourceManagementService
from foundry_lite.application.services.source_management_views import sync_run_view, sync_view
from foundry_lite.application.services.source_schedule_health import SourceScheduleHealth, source_schedule_health
from foundry_lite.application.services.source_schedule_management import (
    update_sync_schedule_row,
    update_sync_schedule_state_row,
)
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

    required_dependencies = (
        "engine",
        "policy",
        "source_management_repository",
        "source_registry_repository",
        "runtime_repository",
    )
    required_collaborators = ("source_management_service", "runtime_service")

    source_management_repository: SourceManagementRepository
    source_registry_repository: SourceRegistryRepository
    runtime_repository: RuntimeRepository
    source_management_service: SourceManagementService
    runtime_service: SourceRuntimeBoundary

    def update_managed_sync_schedule(
        self,
        sync_name: str,
        *,
        schedule: Mapping[str, object],
        expected_config_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        require_write(self.policy, self.runtime_service, ctx, "update_managed_sync_schedule", sync_name)
        require_idempotency_key(idempotency_key)
        normalized = normalized_source_schedule(schedule)
        with self.engine.begin() as conn:
            row = update_sync_schedule_row(
                self,
                self.runtime_service,
                conn,
                ctx,
                sync_name,
                normalized,
                expected_config_fingerprint,
                idempotency_key,
            )
        return sync_view(row)

    def pause_managed_sync_schedule(
        self,
        sync_name: str,
        *,
        expected_config_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._set_managed_sync_schedule_state(
            sync_name, "paused", expected_config_fingerprint, idempotency_key, ctx
        )

    def resume_managed_sync_schedule(
        self,
        sync_name: str,
        *,
        expected_config_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._set_managed_sync_schedule_state(
            sync_name, "active", expected_config_fingerprint, idempotency_key, ctx
        )

    def start_managed_sync_run(
        self,
        sync_name: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        trigger_type: str = "manual",
        batch_limit: int | None = None,
    ) -> dict[str, object]:
        if trigger_type == "recovery":
            return self.start_managed_sync_recovery_run(
                sync_name,
                idempotency_key=idempotency_key,
                ctx=ctx,
                batch_limit=batch_limit,
            )
        if trigger_type != "manual":
            raise ValidationFailed(
                "public managed sync runs support manual or recovery triggers",
                details={"triggerType": trigger_type},
            )
        return self.source_management_service.start_managed_sync_run(
            sync_name,
            idempotency_key=idempotency_key,
            ctx=ctx,
            trigger_type=trigger_type,
            batch_limit=batch_limit,
        )

    def start_managed_sync_recovery_run(
        self,
        sync_name: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        batch_limit: int | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        require_write(self.policy, self.runtime_service, ctx, "start_managed_sync_recovery_run", sync_name)
        require_idempotency_key(idempotency_key)
        replay = self._recovery_replay(ctx, sync_name, idempotency_key)
        if replay is not None:
            return sync_run_view(replay)
        self._require_auto_paused(ctx, sync_name)
        run = self.source_management_service.start_managed_sync_run(
            sync_name,
            idempotency_key=idempotency_key,
            ctx=ctx,
            trigger_type="recovery",
            batch_limit=batch_limit,
        )
        if run.get("status") == "succeeded":
            self._apply_current_schedule_health(ctx, sync_name)
        return run

    def _set_managed_sync_schedule_state(
        self,
        sync_name: str,
        target_status: str,
        expected_config_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        require_write(self.policy, self.runtime_service, ctx, f"{target_status}_managed_sync_schedule", sync_name)
        require_idempotency_key(idempotency_key)
        if target_status == "active":
            self._require_manual_resume_allowed(ctx, sync_name)
        with self.engine.begin() as conn:
            row = update_sync_schedule_state_row(
                self,
                self.runtime_service,
                conn,
                ctx,
                sync_name,
                target_status,
                expected_config_fingerprint,
                idempotency_key,
            )
        return sync_view(row)

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
            runs = self.source_management_repository.list_sync_runs(
                tenant_id=ctx.tenant_id,
                sync_name=str(sync["sync_name"]),
            )
            decisions.append(source_schedule_decision(self._source_aware_sync(ctx, sync), runs, now=now))
        return decisions

    def _run_decisions(self, ctx: RequestContext, now: datetime) -> list[SourceScheduleDecision]:
        decisions: list[SourceScheduleDecision] = []
        for sync in self.source_management_repository.list_syncs(tenant_id=ctx.tenant_id):
            runs = self._reconciled_runs(ctx, sync)
            current = self._apply_schedule_health(ctx, sync, runs)
            decisions.append(source_schedule_decision(self._source_aware_sync(ctx, current), runs, now=now))
        return decisions

    def _source_aware_sync(self, ctx: RequestContext, sync: Mapping[str, object]) -> Mapping[str, object]:
        with self.engine.begin() as conn:
            source = self.source_registry_repository.source_by_name(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                source_name=str(sync["source_name"]),
            )
        return {**sync, "status": "inactive"} if source is not None and source["status"] == "disabled" else sync

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
        if run.get("status") == "failed":
            self._apply_current_schedule_health(ctx, decision.sync_name)
        return {"decision": decision.as_dict(), "run": run}

    def _recovery_replay(
        self, ctx: RequestContext, sync_name: str, idempotency_key: str
    ) -> Mapping[str, object] | None:
        with self.engine.begin() as conn:
            return self.source_management_repository.sync_run_by_idempotency_key(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                sync_name=sync_name,
                idempotency_key=idempotency_key,
            )

    def _require_auto_paused(self, ctx: RequestContext, sync_name: str) -> None:
        sync, runs = self._current_sync_and_runs(ctx, sync_name)
        if not source_schedule_health(sync, runs).is_auto_paused:
            raise ValidationFailed(
                "recovery run requires an automatically paused recurring schedule",
                details={"syncName": sync_name},
            )

    def _require_manual_resume_allowed(self, ctx: RequestContext, sync_name: str) -> None:
        sync, runs = self._current_sync_and_runs(ctx, sync_name)
        health = source_schedule_health(sync, runs)
        if health.is_auto_paused:
            raise ValidationFailed(
                "successful recovery run is required before resuming an automatically paused schedule",
                details={
                    "syncName": sync_name,
                    "consecutiveFailureCount": health.consecutive_failure_count,
                    "autoPauseAfterFailures": health.auto_pause_after_failures,
                },
            )

    def _current_sync_and_runs(
        self, ctx: RequestContext, sync_name: str
    ) -> tuple[Mapping[str, object], Sequence[Mapping[str, object]]]:
        with self.engine.begin() as conn:
            sync = sync_row(self, conn, ctx, sync_name)
        runs = self.source_management_repository.list_sync_runs(tenant_id=ctx.tenant_id, sync_name=sync_name)
        return sync, runs

    def _apply_current_schedule_health(self, ctx: RequestContext, sync_name: str) -> Mapping[str, object]:
        sync, runs = self._current_sync_and_runs(ctx, sync_name)
        return self._apply_schedule_health(ctx, sync, runs)

    def _apply_schedule_health(
        self,
        ctx: RequestContext,
        sync: Mapping[str, object],
        runs: Sequence[Mapping[str, object]],
    ) -> Mapping[str, object]:
        health = source_schedule_health(sync, runs)
        if sync.get("status") == "active" and health.should_auto_pause:
            return self._automatic_schedule_transition(ctx, sync, health, "paused")
        if sync.get("status") == "paused" and health.successful_recovery_run_id:
            return self._automatic_schedule_transition(ctx, sync, health, "active")
        return sync

    def _automatic_schedule_transition(
        self,
        ctx: RequestContext,
        sync: Mapping[str, object],
        health: SourceScheduleHealth,
        target_status: str,
    ) -> Mapping[str, object]:
        event = "auto_paused" if target_status == "paused" else "auto_resumed"
        run_id = health.last_failure_run_id if target_status == "paused" else health.successful_recovery_run_id
        evidence = {**health.as_dict(), "triggerRunId": run_id, "automaticTransition": True}
        correlation_id = f"scheduler:{event}:{sync['sync_name']}:{run_id}"
        with self.engine.begin() as conn:
            return update_sync_schedule_state_row(
                self,
                self.runtime_service,
                conn,
                ctx,
                str(sync["sync_name"]),
                target_status,
                str(sync["config_fingerprint"]),
                correlation_id,
                audit_event=event,
                audit_evidence=evidence,
            )


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
