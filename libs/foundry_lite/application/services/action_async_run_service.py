"""API-facing enqueue, observation, dispatch recovery, and cancellation for Action runs."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping
from typing import cast

from foundry_lite.application.primitives import _now
from foundry_lite.application.services.action_async_payloads import (
    action_event_payload,
    action_run_snapshot,
    decode_action_run_cursor,
    next_action_run_cursor,
)
from foundry_lite.application.services.action_async_run_support import (
    action_dispatch_request,
    action_execution_snapshot,
    action_run_event,
    async_request_fingerprint,
    async_run_record,
    async_run_steps,
)
from foundry_lite.application.services.action_distributed_contracts import (
    ActionAsyncRunRecord,
    ActionAsyncRunRow,
    ActionExecutionRepository,
    ActionRunOrchestrator,
    ConflictDetected,
    MetadataRepository,
    RequestContext,
    TransactionContext,
)
from foundry_lite.application.services.action_planning_service import ActionPlanningService
from foundry_lite.application.services.action_protocols import ActionOntologyLookup
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.errors import NotFound, ValidationFailed
from foundry_lite.security.tenant_context import tenant_context

_TERMINAL = frozenset(
    {"succeeded", "failed", "cancelled", "conflict", "outcome_unknown", "compensation_required", "reconciled"}
)


class ActionAsyncRunService(CoreService):
    """Persist first and return without executing function or commit work in the API process."""

    required_dependencies = (
        "engine",
        "policy",
        "metadata_repository",
        "action_execution_repository",
        "action_run_orchestrator",
    )
    required_collaborators = ("action_planning_service", "ontology_lookup_service", "runtime_service")
    action_execution_repository: ActionExecutionRepository
    action_run_orchestrator: ActionRunOrchestrator
    metadata_repository: MetadataRepository
    action_planning_service: ActionPlanningService
    ontology_lookup_service: ActionOntologyLookup
    runtime_service: RuntimeEvidenceBoundary

    def start(
        self,
        action_api_name: str,
        *,
        object_type: str,
        object_id: str,
        expected_object_version: int,
        params: Mapping[str, object],
        idempotency_key: str,
        wait_seconds: int,
        ctx: RequestContext,
    ) -> dict[str, object]:
        _require_start_values(idempotency_key, wait_seconds)
        self.runtime_service._require_write_traffic_open(
            ctx, operation="start_action_run", resource_type="action_type", resource_id=action_api_name
        )
        request_fingerprint = async_request_fingerprint(
            ctx, action_api_name, object_type, object_id, expected_object_version, params
        )
        existing = self._existing_run(ctx, action_api_name, idempotency_key)
        if existing is not None:
            _require_replay(existing, request_fingerprint)
            self._dispatch(ctx, existing)
            return self._wait_for_snapshot(ctx, str(existing["id"]), wait_seconds)
        plan = self.action_planning_service.plan_action(
            action_api_name,
            object_type=object_type,
            object_id=object_id,
            expected_object_version=expected_object_version,
            params=params,
            ctx=ctx,
        )
        row, is_created = self._create_run(ctx, action_api_name, plan, idempotency_key, request_fingerprint)
        if not is_created:
            _require_replay(row, request_fingerprint)
        self._dispatch(ctx, row)
        return self._wait_for_snapshot(ctx, str(row["id"]), wait_seconds)

    def get(self, run_id: str, *, ctx: RequestContext) -> dict[str, object]:
        self.policy.require(ctx, "action:apply")
        with self.engine.begin() as transaction:
            row = self._required_run(transaction, ctx, run_id)
            steps = self.action_execution_repository.steps_for_run(
                transaction=transaction, tenant_id=ctx.tenant_id, run_id=run_id
            )
            attempts = self.action_execution_repository.attempts_for_run(
                transaction=transaction, tenant_id=ctx.tenant_id, run_id=run_id
            )
            effects = self.action_execution_repository.effect_receipts_for_run(
                transaction=transaction, tenant_id=ctx.tenant_id, run_id=run_id
            )
        return action_run_snapshot(row, steps, attempts, effects)

    def list_runs(self, *, cursor: str | None, limit: int, ctx: RequestContext) -> dict[str, object]:
        self.policy.require(ctx, "action:apply")
        created_at, run_id = decode_action_run_cursor(cursor)
        bounded = max(1, min(limit, 100))
        with self.engine.begin() as transaction:
            rows = self.action_execution_repository.list_runs(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                before_created_at=created_at,
                before_run_id=run_id,
                limit=bounded + 1,
            )
        page = rows[:bounded]
        return {
            "items": [self.get(str(row["id"]), ctx=ctx) for row in page],
            "nextCursor": next_action_run_cursor(rows, bounded),
        }

    def events(self, run_id: str, *, after_sequence: int, limit: int, ctx: RequestContext) -> dict[str, object]:
        self.policy.require(ctx, "action:apply")
        with self.engine.begin() as transaction:
            self._required_run(transaction, ctx, run_id)
            rows = self.action_execution_repository.run_events(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                run_id=run_id,
                after_sequence=max(0, after_sequence),
                limit=max(1, min(limit, 500)),
            )
        return {"actionRunId": run_id, "events": [action_event_payload(row) for row in rows]}

    def cancel(
        self, run_id: str, *, idempotency_key: str, reason: str | None, ctx: RequestContext
    ) -> dict[str, object]:
        if not idempotency_key.strip():
            raise ValidationFailed("Idempotency-Key is required")
        fingerprint = _cancel_fingerprint(run_id, reason)
        row = self._request_cancel(ctx, run_id, idempotency_key, fingerprint, reason)
        workflow_id = row["workflow_run_id"]
        if row["status"] == "cancelling" and isinstance(workflow_id, str):
            self.action_run_orchestrator.cancel(ctx.tenant_id, workflow_id, reason=reason)
        return self.get(run_id, ctx=ctx)

    def recover_dispatches(self, *, tenant_id: str, limit: int = 100) -> dict[str, object]:
        with self.engine.begin() as transaction:
            rows = self.action_execution_repository.pending_dispatches(
                transaction=transaction, tenant_id=tenant_id, limit=max(1, min(limit, 500))
            )
        for row in rows:
            self._dispatch(_worker_context(row), row)
        return {"recovered": len(rows)}

    def recover_all_dispatches(self, *, limit: int = 100) -> dict[str, object]:
        recovered = 0
        for tenant_id in self.metadata_repository.list_tenant_ids():
            with tenant_context(tenant_id):
                result = self.recover_dispatches(tenant_id=tenant_id, limit=limit)
                recovered += cast(int, result["recovered"])
        return {"recovered": recovered}

    def _create_run(
        self,
        ctx: RequestContext,
        action_api_name: str,
        plan: Mapping[str, object],
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[ActionAsyncRunRow, bool]:
        with self.engine.begin() as transaction:
            action_type = self.ontology_lookup_service._active_action_type(transaction, ctx, action_api_name)
            snapshot = action_execution_snapshot(action_type, plan, ctx)
            record = async_run_record(ctx, action_type, plan, snapshot, idempotency_key, request_fingerprint)
            row = self.action_execution_repository.insert_run(
                transaction=transaction, record=record, steps=async_run_steps(record, plan)
            )
            if row is None:
                return self._idempotent_winner(transaction, ctx, record), False
            self._record_queued(transaction, ctx, row)
            return row, True

    def _existing_run(
        self, ctx: RequestContext, action_api_name: str, idempotency_key: str
    ) -> ActionAsyncRunRow | None:
        self.policy.require(ctx, "action:apply")
        with self.engine.begin() as transaction:
            action_type = self.ontology_lookup_service._active_action_type(transaction, ctx, action_api_name)
            return self.action_execution_repository.run_by_idempotency_key(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                action_type_id=action_type["id"],
                actor_user_id=ctx.actor_user_id,
                idempotency_key=idempotency_key,
            )

    def _idempotent_winner(
        self, transaction: TransactionContext, ctx: RequestContext, record: ActionAsyncRunRecord
    ) -> ActionAsyncRunRow:
        row = self.action_execution_repository.run_by_idempotency_key(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            action_type_id=record.action_type_id,
            actor_user_id=ctx.actor_user_id,
            idempotency_key=record.idempotency_key,
        )
        if row is None:
            raise ConflictDetected("Action run idempotency winner could not be loaded")
        return row

    def _record_queued(self, transaction: TransactionContext, ctx: RequestContext, row: ActionAsyncRunRow) -> None:
        for event_type in ("action.run.received", "action.run.validated", "action.run.planned", "action.run.queued"):
            self.action_execution_repository.append_event(
                transaction=transaction, record=action_run_event(ctx, str(row["id"]), event_type, {})
            )
        self.runtime_service._audit(
            transaction,
            ctx,
            event_type="action.run.queued",
            resource_type="action_run",
            resource_id=str(row["id"]),
            action="start",
            after_ref={"planHash": row["plan_hash"]},
            correlation_id=str(row["id"]),
        )
        self.runtime_service._outbox(
            transaction,
            ctx,
            "action.run.queued",
            "action_run",
            str(row["id"]),
            {"actionRunId": row["id"], "planHash": row["plan_hash"]},
            idempotency_key=f"action.run.queued:{row['id']}",
            correlation_id=str(row["id"]),
        )

    def _dispatch(self, ctx: RequestContext, row: ActionAsyncRunRow) -> None:
        if row["status"] != "queued" or row["dispatch_status"] == "dispatched":
            return
        result = self.action_run_orchestrator.dispatch(action_dispatch_request(ctx, row))
        dispatch_status = "unknown" if result.status == "unknown" else "dispatched"
        error: dict[str, object] | None = {"kind": "dispatch_unknown"} if dispatch_status == "unknown" else None
        with self.engine.begin() as transaction:
            updated = self.action_execution_repository.update_dispatch(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                run_id=str(row["id"]),
                workflow_run_id=result.workflow_run_id,
                dispatch_status=dispatch_status,
                dispatch_error=error,
            )
            if updated is not None:
                event_type = f"action.run.dispatch_{dispatch_status}"
                self.action_execution_repository.append_event(
                    transaction=transaction,
                    record=action_run_event(ctx, str(row["id"]), event_type, {"taskQueue": result.task_queue}),
                )

    def _request_cancel(
        self, ctx: RequestContext, run_id: str, idempotency_key: str, fingerprint: str, reason: str | None
    ) -> ActionAsyncRunRow:
        with self.engine.begin() as transaction:
            current = self._required_run(transaction, ctx, run_id)
            _require_cancel_replay(current, idempotency_key, fingerprint)
            if current["status"] in _TERMINAL or current["status"] == "cancelling":
                return current
            updated = self.action_execution_repository.request_cancel(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                run_id=run_id,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                reason=reason,
                requested_at=_now(),
            )
            if updated is None:
                raise ConflictDetected("Action run cancellation changed concurrently")
            self.action_execution_repository.append_event(
                transaction=transaction,
                record=action_run_event(ctx, run_id, "action.run.cancelling", {"reason": reason}),
            )
            self.runtime_service._audit(
                transaction,
                ctx,
                event_type="action.run.cancelling",
                resource_type="action_run",
                resource_id=run_id,
                action="cancel",
                after_ref={"reason": reason},
                correlation_id=run_id,
            )
            return updated

    def _required_run(self, transaction: TransactionContext, ctx: RequestContext, run_id: str) -> ActionAsyncRunRow:
        row = self.action_execution_repository.run_by_id(
            transaction=transaction, tenant_id=ctx.tenant_id, run_id=run_id
        )
        if row is None or row["execution_mode"] != "async":
            raise NotFound("Action run not found", details={"actionRunId": run_id})
        return row

    def _wait_for_snapshot(self, ctx: RequestContext, run_id: str, wait_seconds: int) -> dict[str, object]:
        deadline = time.monotonic() + wait_seconds
        while True:
            snapshot = self.get(run_id, ctx=ctx)
            if snapshot["status"] in _TERMINAL or time.monotonic() >= deadline:
                return snapshot
            time.sleep(0.1)


def _require_start_values(idempotency_key: str, wait_seconds: int) -> None:
    if not idempotency_key.strip():
        raise ValidationFailed("Idempotency-Key is required")
    if wait_seconds < 0 or wait_seconds > 30:
        raise ValidationFailed("waitSeconds must be between 0 and 30")


def _require_replay(row: ActionAsyncRunRow, request_fingerprint: str) -> None:
    if row["request_fingerprint"] != request_fingerprint:
        raise ConflictDetected("Idempotency-Key was already used with a different Action request")


def _cancel_fingerprint(run_id: str, reason: str | None) -> str:
    raw = f"{run_id}\0{reason or ''}".encode()
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _require_cancel_replay(row: ActionAsyncRunRow, key: str, fingerprint: str) -> None:
    if row["cancel_idempotency_key"] is None:
        return
    if row["cancel_idempotency_key"] != key or row["cancel_request_fingerprint"] != fingerprint:
        raise ConflictDetected("Cancellation Idempotency-Key was reused with a different request")


def _worker_context(row: ActionAsyncRunRow) -> RequestContext:
    return RequestContext(
        tenant_id=str(row["tenant_id"]),
        actor_user_id="action-control-worker",
        request_id=f"action-dispatch-recovery:{row['id']}",
        roles=("admin",),
    )
