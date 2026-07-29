"""Durable cron and interval scheduling for deployed Pipeline versions."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import cast

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.pipeline_repository import (
    PipelineRepository,
    PipelineScheduleOperationRecord,
    PipelineScheduleRow,
)
from foundry_lite.application.primitives import _new_id
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.pipeline_async_run_service import PipelineAsyncRunService
from foundry_lite.application.services.pipeline_execution_contracts import PipelineScheduleSpec
from foundry_lite.application.services.pipeline_payloads import (
    bounded_pipeline_limit,
    schedule_payload,
    schedule_record,
)
from foundry_lite.application.services.pipeline_run_requests import require_deployed, require_pipeline_match
from foundry_lite.application.services.pipeline_schedule_runtime import (
    initial_next_due_at,
    normalize_legacy_pipeline_schedule,
    normalize_pipeline_schedule,
    schedule_iso,
    schedule_operation_fingerprint,
    schedule_spec_payload,
    scheduled_run_idempotency_key,
    scheduler_now,
)
from foundry_lite.application.services.pipeline_scheduler_evidence import (
    record_pipeline_schedule_event,
    require_pipeline_schedule_write,
)
from foundry_lite.application.services.pipeline_scheduler_queries import (
    list_due_schedule_rows,
    require_pipeline_schedule,
    require_schedule_version,
)
from foundry_lite.application.services.pipeline_scheduler_results import (
    ScheduleOperationReservation,
    completion_values,
    lease_lost_result,
    replayed_operation,
    status_next_due,
    tick_result,
    upsert_request,
)
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed

_LEASE_SECONDS = 60
_RECONCILIATION_LIMIT = 100
_SUCCESS_STATUSES = {"queued", "running", "succeeded"}
_SKIPPED_REASONS = {"lease_not_acquired", "run_in_progress"}


class PipelineSchedulerService(CoreService):
    required_dependencies = ("engine", "policy", "pipeline_repository")
    required_collaborators = ("pipeline_async_run_service", "runtime_service")
    pipeline_repository: PipelineRepository
    pipeline_async_run_service: PipelineAsyncRunService
    runtime_service: RuntimeEvidenceBoundary

    def upsert_schedule(
        self,
        pipeline_id: str,
        *,
        version_id: str,
        schedule: Mapping[str, object],
        enabled: bool,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        require_pipeline_schedule_write(self.runtime_service, ctx, "upsert_pipeline_schedule", pipeline_id)
        now = scheduler_now()
        request = upsert_request(version_id, schedule, enabled)
        spec = normalize_pipeline_schedule(schedule, is_enabled=enabled, now=now)
        fingerprint = schedule_operation_fingerprint(pipeline_id, "upsert", request)
        with self.engine.begin() as conn:
            reservation = self._reserve_operation(conn, ctx, pipeline_id, "upsert", idempotency_key, fingerprint)
            if reservation.replay_result is not None:
                return reservation.replay_result
            version = require_schedule_version(self.pipeline_repository, conn, ctx, version_id)
            require_pipeline_match(version, pipeline_id)
            require_deployed(version)
            row = self._upsert_row(conn, ctx, pipeline_id, version_id, spec, now)
            result = cast(dict[str, object], schedule_payload(row))
            self._complete_mutation(conn, ctx, reservation, row, "upserted", result, idempotency_key)
            return result

    def get_schedule(self, pipeline_id: str, *, ctx: RequestContext | None = None) -> dict[str, object] | None:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        with self.engine.begin() as conn:
            row = self.pipeline_repository.schedule_by_pipeline(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                pipeline_id=pipeline_id,
            )
        return schedule_payload(row)

    def pause_schedule(
        self,
        pipeline_id: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._set_schedule_status(
            pipeline_id,
            target_status="paused",
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def resume_schedule(
        self,
        pipeline_id: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        return self._set_schedule_status(
            pipeline_id,
            target_status="active",
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def delete_schedule(
        self,
        pipeline_id: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        require_pipeline_schedule_write(self.runtime_service, ctx, "delete_pipeline_schedule", pipeline_id)
        fingerprint = schedule_operation_fingerprint(pipeline_id, "delete", {})
        with self.engine.begin() as conn:
            reservation = self._reserve_operation(
                conn,
                ctx,
                pipeline_id,
                "delete",
                idempotency_key,
                fingerprint,
            )
            if reservation.replay_result is not None:
                return reservation.replay_result
            before = self.pipeline_repository.schedule_by_pipeline(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                pipeline_id=pipeline_id,
            )
            deleted = self.pipeline_repository.delete_schedule(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                pipeline_id=pipeline_id,
            )
            result: dict[str, object] = {"deleted": deleted}
            self._complete_mutation(conn, ctx, reservation, before, "deleted", result, idempotency_key)
            return result

    def preview_due_schedules(
        self,
        *,
        max_runs: int = 50,
        now: datetime | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "pipeline:read")
        limit = bounded_pipeline_limit(max_runs)
        evaluated_at = scheduler_now(now)
        with self.engine.begin() as conn:
            rows = self.pipeline_repository.list_due_schedules(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                due_at=schedule_iso(evaluated_at),
                limit=limit,
            )
        return {
            "items": [schedule_payload(row) for row in rows],
            "evaluatedAt": schedule_iso(evaluated_at),
            "maxRuns": limit,
        }

    def tick_schedules(
        self,
        *,
        max_runs: int = 50,
        now: datetime | None = None,
        scheduler_owner: str | None = None,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        require_pipeline_schedule_write(self.runtime_service, ctx, "tick_pipeline_schedules", "tick")
        limit = bounded_pipeline_limit(max_runs)
        evaluated_at = scheduler_now(now)
        owner = scheduler_owner or f"pipeline-scheduler:{ctx.request_id}"
        reconciled = self._reconcile_legacy_schedules(ctx, evaluated_at)
        candidates = list_due_schedule_rows(
            self.engine,
            self.pipeline_repository,
            tenant_id=ctx.tenant_id,
            due_at=schedule_iso(evaluated_at),
            limit=limit,
        )
        results = [self._claim_and_run(ctx, row, evaluated_at, owner) for row in candidates]
        started = [result for result in results if result.get("reason") not in _SKIPPED_REASONS]
        skipped = [result for result in results if result.get("reason") in _SKIPPED_REASONS]
        return {
            "status": "evaluated",
            "evaluatedAt": schedule_iso(evaluated_at),
            "evaluated": len(candidates),
            "started": started,
            "skipped": skipped,
            "reconciled": reconciled,
            "maxRuns": limit,
        }

    def _set_schedule_status(
        self,
        pipeline_id: str,
        *,
        target_status: str,
        idempotency_key: str,
        ctx: RequestContext | None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        operation = "resume" if target_status == "active" else "pause"
        require_pipeline_schedule_write(
            self.runtime_service,
            ctx,
            f"{operation}_pipeline_schedule",
            pipeline_id,
        )
        fingerprint = schedule_operation_fingerprint(pipeline_id, operation, {})
        now = scheduler_now()
        with self.engine.begin() as conn:
            reservation = self._reserve_operation(conn, ctx, pipeline_id, operation, idempotency_key, fingerprint)
            if reservation.replay_result is not None:
                return reservation.replay_result
            current = require_pipeline_schedule(self.pipeline_repository, conn, ctx, pipeline_id)
            next_due_at = status_next_due(current, target_status, now)
            row = self._update_status_row(conn, ctx, current, target_status, next_due_at, now)
            result = cast(dict[str, object], schedule_payload(row))
            self._complete_mutation(conn, ctx, reservation, row, operation + "d", result, idempotency_key)
            return result

    def _reserve_operation(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        pipeline_id: str,
        operation: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> ScheduleOperationReservation:
        if not idempotency_key.strip():
            raise ValidationFailed("Idempotency-Key is required")
        row, is_created = self.pipeline_repository.reserve_schedule_operation(
            transaction=conn,
            record=PipelineScheduleOperationRecord(
                operation_id=_new_id("psop"),
                tenant_id=ctx.tenant_id,
                pipeline_id=pipeline_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                created_by=ctx.actor_user_id,
                created_at=schedule_iso(scheduler_now()),
            ),
        )
        if is_created:
            return ScheduleOperationReservation(str(row["id"]), None)
        return ScheduleOperationReservation(None, replayed_operation(row, fingerprint))

    def _complete_mutation(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        reservation: ScheduleOperationReservation,
        row: PipelineScheduleRow | None,
        event: str,
        result: dict[str, object],
        idempotency_key: str,
    ) -> None:
        if reservation.operation_id is None:
            raise ConflictDetected("pipeline schedule operation reservation is missing")
        completed = self.pipeline_repository.complete_schedule_operation(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            operation_id=reservation.operation_id,
            result=result,
        )
        if completed is None:
            raise ConflictDetected("pipeline schedule operation result was not persisted")
        record_pipeline_schedule_event(self.runtime_service, conn, ctx, row, event, result, idempotency_key)

    def _upsert_row(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        pipeline_id: str,
        version_id: str,
        spec: PipelineScheduleSpec,
        now: datetime,
    ) -> PipelineScheduleRow:
        status = "active" if spec.is_enabled else "paused"
        return self.pipeline_repository.upsert_schedule(
            transaction=conn,
            record=schedule_record(
                ctx,
                pipeline_id=pipeline_id,
                version_id=version_id,
                schedule=schedule_spec_payload(spec),
                enabled=spec.is_enabled,
                status=status,
                trigger_type=spec.trigger_kind,
                timezone=spec.timezone,
                next_due_at=initial_next_due_at(spec, now),
                paused_reason=None if spec.is_enabled else "disabled_on_upsert",
                now=schedule_iso(now),
            ),
        )

    def _update_status_row(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        current: PipelineScheduleRow,
        target_status: str,
        next_due_at: str | None,
        now: datetime,
    ) -> PipelineScheduleRow:
        row = self.pipeline_repository.update_schedule_status(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            pipeline_id=str(current["pipeline_id"]),
            status=target_status,
            enabled=target_status == "active",
            next_due_at=next_due_at,
            paused_reason=None if target_status == "active" else "manual",
            updated_by=ctx.actor_user_id,
            updated_at=schedule_iso(now),
        )
        if row is None:
            raise NotFound("pipeline schedule not found", details={"pipeline_id": current["pipeline_id"]})
        return row

    def _reconcile_legacy_schedules(
        self,
        ctx: RequestContext,
        now: datetime,
    ) -> int:
        observed_at = schedule_iso(now)
        with self.engine.begin() as conn:
            rows = self.pipeline_repository.list_schedules_needing_reconciliation(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                observed_at=observed_at,
                limit=_RECONCILIATION_LIMIT,
            )
            return sum(self._reconcile_legacy_schedule(conn, ctx, row, now) for row in rows)

    def _reconcile_legacy_schedule(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        row: PipelineScheduleRow,
        now: datetime,
    ) -> int:
        spec, is_fallback = normalize_legacy_pipeline_schedule(
            row["schedule"],
            is_enabled=bool(row["enabled"]),
            now=now,
        )
        canonical_schedule = schedule_spec_payload(spec)
        status = "active" if spec.is_enabled else "paused"
        reconciled = self.pipeline_repository.reconcile_schedule_runtime(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            schedule_id=str(row["id"]),
            expected_updated_at=str(row["updated_at"]),
            observed_at=schedule_iso(now),
            schedule=canonical_schedule,
            status=status,
            trigger_type=spec.trigger_kind,
            timezone=spec.timezone,
            next_due_at=initial_next_due_at(spec, now),
            paused_reason=None if spec.is_enabled else "disabled_by_legacy_writer",
        )
        if reconciled is None:
            return 0
        payload = cast(dict[str, object], schedule_payload(reconciled))
        payload["reconciliation"] = {
            "legacyWriterUpdatedAt": row["updated_at"],
            "originalSchedule": dict(row["schedule"]),
            "fallbackApplied": is_fallback,
        }
        key = f"pipeline-schedule-reconcile:{row['id']}:{row['updated_at']}"
        record_pipeline_schedule_event(self.runtime_service, conn, ctx, reconciled, "reconciled", payload, key)
        return 1

    def _claim_and_run(
        self,
        ctx: RequestContext,
        candidate: PipelineScheduleRow,
        now: datetime,
        owner: str,
    ) -> dict[str, object]:
        claimed = self._claim_schedule(ctx, candidate, now, owner)
        if claimed is None:
            return {"scheduleId": candidate["id"], "reason": "lease_not_acquired"}
        slot_start = str(claimed["next_due_at"])
        key = scheduled_run_idempotency_key(str(claimed["id"]), slot_start)
        try:
            run = self.pipeline_async_run_service.enqueue(
                str(claimed["pipeline_id"]),
                version_id=str(claimed["version_id"]),
                idempotency_key=key,
                parameters={"scheduleId": claimed["id"], "slotStart": slot_start},
                target_node_ids=None,
                wait_seconds=0,
                ctx=ctx,
            )
        except Exception as exc:
            error = dict(self.runtime_service._error_payload(exc, ctx, correlation_id=key))
            return self._finish_claim(ctx, claimed, slot_start, None, error, now)
        run_error = cast(dict[str, object] | None, run.get("error"))
        return self._finish_claim(ctx, claimed, slot_start, run, run_error, now)

    def _claim_schedule(
        self,
        ctx: RequestContext,
        candidate: PipelineScheduleRow,
        now: datetime,
        owner: str,
    ) -> PipelineScheduleRow | None:
        lease_token = _new_id("pslease")
        lease_expires_at = schedule_iso(now + timedelta(seconds=_LEASE_SECONDS))
        with self.engine.begin() as conn:
            return self.pipeline_repository.claim_due_schedule(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                schedule_id=str(candidate["id"]),
                due_at=schedule_iso(now),
                lease_owner=owner,
                lease_token=lease_token,
                lease_expires_at=lease_expires_at,
            )

    def _finish_claim(
        self,
        ctx: RequestContext,
        claimed: PipelineScheduleRow,
        slot_start: str,
        run: dict[str, object] | None,
        error: dict[str, object] | None,
        now: datetime,
    ) -> dict[str, object]:
        is_enqueued = run is not None and str(run.get("status")) in _SUCCESS_STATUSES
        values = completion_values(claimed, slot_start, run, error, now, is_enqueued)
        with self.engine.begin() as conn:
            completed = self.pipeline_repository.complete_schedule_tick(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                schedule_id=str(claimed["id"]),
                lease_token=str(claimed["lease_token"]),
                fencing_token=int(claimed["fencing_token"]),
                values=values,
            )
            if completed is None:
                return lease_lost_result(claimed, slot_start, run)
            result = tick_result(completed, slot_start, run, error)
            record_pipeline_schedule_event(
                self.runtime_service,
                conn,
                ctx,
                completed,
                "triggered",
                result,
                scheduled_run_idempotency_key(str(claimed["id"]), slot_start),
            )
            return result
