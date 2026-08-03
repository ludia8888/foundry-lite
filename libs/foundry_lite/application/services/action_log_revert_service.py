"""Normalized Action log queries and latest-edit-safe atomic revert."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from datetime import datetime

from foundry_lite.application.action_log_types import (
    ActionLogEntryRow,
    ActionRevertEligibility,
)
from foundry_lite.application.ports import ACTION_RUN_SUCCEEDED, ActionRepository, TransactionContext
from foundry_lite.application.ports.action_execution_repository import ActionExecutionRepository
from foundry_lite.application.ports.action_repository import ActionRunRecord, ActionRunRow, ObjectEditRow
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.action_edit_plan_results import CommittedEdit
from foundry_lite.application.services.action_log_payloads import (
    action_log_payload,
    decode_action_log_cursor,
    next_action_log_cursor,
)
from foundry_lite.application.services.action_log_writer import record_action_log
from foundry_lite.application.services.action_protocols import ActionObjectIndexer, ActionRuntimeBoundary
from foundry_lite.application.services.action_revert_mutations import apply_inverse_edit
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.ontology_lookup_service import OntologyLookupService
from foundry_lite.domain.action_runtime.action_contract import compile_action_contract
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed


class ActionLogRevertService(CoreService):
    """Query Action logs and restore eligible internal Ontology edits atomically."""

    required_dependencies = ("engine", "policy", "action_repository", "action_execution_repository")
    required_collaborators = ("object_index_record_mutation_service", "ontology_lookup_service", "runtime_service")
    action_repository: ActionRepository
    action_execution_repository: ActionExecutionRepository
    object_index_record_mutation_service: ActionObjectIndexer
    ontology_lookup_service: OntologyLookupService
    runtime_service: ActionRuntimeBoundary

    def list_logs(self, *, cursor: str | None, limit: int, ctx: RequestContext) -> dict[str, object]:
        """Return a permission-filtered cursor page of normalized Action logs."""
        self.policy.require(ctx, "action:log:read")
        created_at, log_id = decode_action_log_cursor(cursor)
        bounded = max(1, min(limit, 100))
        with self.engine.begin() as transaction:
            rows = self.action_repository.list_action_logs(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                before_created_at=created_at,
                before_log_id=log_id,
                limit=bounded + 1,
            )
            items = [self._log_payload(transaction, ctx, row) for row in rows[:bounded]]
            monitoring = self._monitoring(transaction, ctx)
        return {"items": items, "nextCursor": next_action_log_cursor(rows, bounded), "monitoring": monitoring}

    def _monitoring(self, transaction: TransactionContext, ctx: RequestContext) -> dict[str, object]:
        window_limit = 1_000
        rows = self.action_repository.action_runs_for_monitoring(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            limit=window_limit + 1,
        )
        effect_counts = self.action_execution_repository.effect_status_counts(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
        )
        return _monitoring_payload(rows, effect_counts, window_limit)

    def revert_eligibility(self, run_id: str, *, ctx: RequestContext) -> ActionRevertEligibility:
        """Explain whether all edits from one run are still the latest and reversible."""
        self.policy.require(ctx, "action:revert")
        with self.engine.begin() as transaction:
            run, log, edits = self._revert_source(transaction, ctx, run_id)
            return self._eligibility(transaction, ctx, run, log, edits)

    def revert(self, run_id: str, *, idempotency_key: str, ctx: RequestContext) -> dict[str, object]:
        """Atomically apply all inverse edits and record a new non-revertible Action run."""
        self.policy.require(ctx, "action:revert")
        if not idempotency_key.strip():
            raise ValidationFailed("Idempotency-Key is required")
        with self.engine.begin() as transaction:
            original, log, edits = self._revert_source(transaction, ctx, run_id)
            existing = self._existing_revert(transaction, ctx, original, run_id, idempotency_key)
            if existing is not None:
                return self._revert_response(existing, run_id)
            eligibility = self._eligibility(transaction, ctx, original, log, edits)
            if not eligibility["isEligible"]:
                raise ConflictDetected("Action run is not revertible", details=dict(eligibility))
            revert_run_id = self._insert_revert_run(transaction, ctx, original, run_id, idempotency_key)
            committed = self._apply_inverse_edits(transaction, ctx, revert_run_id, edits)
            result = self._finalize_revert(transaction, ctx, original, log, revert_run_id, run_id, committed)
        return result

    def _revert_source(
        self, transaction: TransactionContext, ctx: RequestContext, run_id: str
    ) -> tuple[ActionRunRow, ActionLogEntryRow, list[ObjectEditRow]]:
        run = self.action_repository.action_run_by_id(
            transaction=transaction, tenant_id=ctx.tenant_id, action_run_id=run_id
        )
        log = self.action_repository.action_log_by_run_id(
            transaction=transaction, tenant_id=ctx.tenant_id, action_run_id=run_id
        )
        if run is None or log is None:
            raise NotFound("Action run log not found", details={"actionRunId": run_id})
        edits = self.action_repository.object_edits_for_run(
            transaction=transaction, tenant_id=ctx.tenant_id, action_run_id=run_id
        )
        return run, log, edits

    def _eligibility(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        run: ActionRunRow,
        log: ActionLogEntryRow,
        edits: list[ObjectEditRow],
    ) -> ActionRevertEligibility:
        reason = self._policy_blocker(ctx, run, log, edits)
        if reason is None:
            reason = next(
                (blocked for edit in edits if (blocked := self._edit_blocker(transaction, ctx, run, edit))), None
            )
        effects = self.action_execution_repository.effect_receipts_for_run(
            transaction=transaction, tenant_id=ctx.tenant_id, run_id=run["id"]
        )
        return {
            "actionRunId": run["id"],
            "isEligible": reason is None,
            "reason": reason,
            "editCount": len(edits),
            "hasPreservedExternalEffects": bool(effects),
            "logEntryId": log["id"],
        }

    def _policy_blocker(
        self, ctx: RequestContext, run: ActionRunRow, log: ActionLogEntryRow, edits: list[ObjectEditRow]
    ) -> str | None:
        if run["status"] != "succeeded" or log["status"] != "succeeded":
            return "action_run_not_succeeded"
        if run["actor_user_id"] != ctx.actor_user_id:
            return "only_original_actor_may_revert"
        if not log["revert_allowed"]:
            return "revert_not_enabled_by_action_definition"
        if log["revert_status"] != "eligible" or log["reverted_by_run_id"] is not None:
            return "action_run_already_reverted"
        if not edits:
            return "action_run_has_no_reversible_edits"
        return None

    def _edit_blocker(
        self, transaction: TransactionContext, ctx: RequestContext, run: ActionRunRow, edit: ObjectEditRow
    ) -> str | None:
        latest = self.action_repository.latest_object_edit(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            object_type_id=edit["object_type_id"],
            object_id=edit["object_id"],
        )
        if latest is None or latest["action_run_id"] != run["id"]:
            return "later_edit_touched_affected_object"
        payload = edit["revert_payload"]
        if not isinstance(payload, Mapping):
            return "revert_evidence_missing"
        operation = payload.get("operation")
        if operation in {"set_property", "create_object", "delete_object"}:
            return self._object_state_blocker(transaction, ctx, edit, payload)
        if operation in {"create_link", "delete_link"}:
            return self._link_state_blocker(transaction, ctx, payload)
        return "revert_operation_unsupported"

    def _object_state_blocker(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        edit: ObjectEditRow,
        payload: Mapping[str, object],
    ) -> str | None:
        row = self.action_repository.object_target_for_revert(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            object_type_id=edit["object_type_id"],
            object_id=edit["object_id"],
        )
        expected = payload.get("committedObjectVersion")
        if row is None or not isinstance(expected, int) or row["object_version"] != expected:
            return "affected_object_version_drifted"
        should_be_deleted = payload.get("operation") == "delete_object"
        if row["deleted"] != should_be_deleted:
            return "affected_object_state_drifted"
        return None

    def _link_state_blocker(
        self, transaction: TransactionContext, ctx: RequestContext, payload: Mapping[str, object]
    ) -> str | None:
        values = [payload.get(key) for key in ("linkTypeId", "fromObjectId", "toObjectId")]
        if not all(isinstance(value, str) and value for value in values):
            return "link_revert_evidence_invalid"
        link = self.action_repository.object_link_for_revert(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            link_type_id=str(values[0]),
            from_object_id=str(values[1]),
            to_object_id=str(values[2]),
        )
        expected_active = payload.get("expectedActive")
        is_active = bool(link and link["is_active"] and not link["deleted"])
        return (
            None
            if isinstance(expected_active, bool) and is_active == expected_active
            else "affected_link_state_drifted"
        )

    def _existing_revert(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        original: ActionRunRow,
        original_run_id: str,
        idempotency_key: str,
    ) -> ActionRunRow | None:
        existing = self.action_repository.action_run_by_idempotency(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            action_type_id=original["action_type_id"],
            actor_user_id=ctx.actor_user_id,
            idempotency_key=_revert_key(original_run_id, idempotency_key),
        )
        if existing is not None and existing["parameters"].get("revertOfActionRunId") != original_run_id:
            raise ConflictDetected("Action revert idempotency key was reused for a different request")
        return existing

    def _insert_revert_run(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        original: ActionRunRow,
        original_run_id: str,
        idempotency_key: str,
    ) -> str:
        revert_run_id = _new_id("action_run")
        now = _now()
        self.action_repository.insert_action_run(
            transaction=transaction,
            record=ActionRunRecord(
                action_run_id=revert_run_id,
                tenant_id=ctx.tenant_id,
                action_type_id=original["action_type_id"],
                action_type_api_name=original["action_type_api_name"],
                actor_user_id=ctx.actor_user_id,
                target_object_type_id=original["target_object_type_id"],
                target_object_type_api_name=original["target_object_type_api_name"],
                target_object_id=original["target_object_id"],
                expected_object_version=original["expected_object_version"],
                parameters={"revertOfActionRunId": original_run_id},
                status="received",
                idempotency_key=_revert_key(original_run_id, idempotency_key),
                request_fingerprint=_revert_fingerprint(original_run_id),
                result=None,
                error=None,
                created_at=now,
                completed_at=None,
            ),
        )
        return revert_run_id

    def _apply_inverse_edits(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        revert_run_id: str,
        edits: list[ObjectEditRow],
    ) -> tuple[CommittedEdit, ...]:
        return tuple(
            apply_inverse_edit(
                self.action_repository,
                self.object_index_record_mutation_service,
                transaction,
                ctx,
                revert_run_id,
                edit,
            )
            for edit in reversed(edits)
        )

    def _finalize_revert(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        original: ActionRunRow,
        original_log: ActionLogEntryRow,
        revert_run_id: str,
        original_run_id: str,
        edits: tuple[CommittedEdit, ...],
    ) -> dict[str, object]:
        effects = self.action_execution_repository.effect_receipts_for_run(
            transaction=transaction, tenant_id=ctx.tenant_id, run_id=original_run_id
        )
        result = {
            "status": "succeeded",
            "revertOfActionRunId": original_run_id,
            "editCount": len(edits),
            "hasPreservedExternalEffects": bool(effects),
        }
        updated = self.action_repository.update_action_run_terminal(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            action_run_id=revert_run_id,
            transition=ACTION_RUN_SUCCEEDED,
            error=None,
            completed_at=_now(),
            result=result,
        )
        if not updated or not self.action_repository.mark_action_log_reverted(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            action_run_id=original_run_id,
            reverted_by_run_id=revert_run_id,
        ):
            raise ConflictDetected("Action revert lost a concurrent commit race")
        self._record_revert_log(transaction, ctx, original, original_log, revert_run_id, edits)
        self._record_revert_evidence(transaction, ctx, original_run_id, revert_run_id, edits, bool(effects))
        return {"actionRunId": revert_run_id, **result}

    def _record_revert_log(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        original: ActionRunRow,
        original_log: ActionLogEntryRow,
        revert_run_id: str,
        edits: tuple[CommittedEdit, ...],
    ) -> None:
        action_type = self.ontology_lookup_service._active_action_type(
            transaction, ctx, original["action_type_api_name"]
        )
        record_action_log(
            self.action_repository,
            transaction,
            ctx,
            revert_run_id,
            compile_action_contract(action_type["definition"]),
            edits,
            definition_version_override=original_log["definition_version"],
            is_revert_allowed_override=False,
        )

    def _record_revert_evidence(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        original_run_id: str,
        revert_run_id: str,
        edits: tuple[CommittedEdit, ...],
        has_external_effects: bool,
    ) -> None:
        payload = {
            "revertOfActionRunId": original_run_id,
            "editCount": len(edits),
            "hasPreservedExternalEffects": has_external_effects,
        }
        self.runtime_service._outbox(
            transaction,
            ctx,
            "action.run.reverted",
            "action_run",
            revert_run_id,
            payload,
            idempotency_key=f"action.run.reverted:{original_run_id}",
            correlation_id=revert_run_id,
        )
        self.runtime_service._audit(
            transaction,
            ctx,
            event_type="action.run.reverted",
            resource_type="action_run",
            resource_id=revert_run_id,
            action="revert",
            after_ref=payload,
            correlation_id=revert_run_id,
        )

    def _log_payload(
        self, transaction: TransactionContext, ctx: RequestContext, row: ActionLogEntryRow
    ) -> dict[str, object]:
        objects = self.action_repository.action_log_objects(
            transaction=transaction, tenant_id=ctx.tenant_id, action_log_entry_id=row["id"]
        )
        run = self.action_repository.action_run_by_id(
            transaction=transaction, tenant_id=ctx.tenant_id, action_run_id=row["action_run_id"]
        )
        object_type = run["target_object_type_api_name"] if run else "unknown"
        parameters = self.policy.mask_sensitive_properties(ctx, object_type, dict(row["parameters"]))
        effects = self.action_execution_repository.effect_receipts_for_run(
            transaction=transaction, tenant_id=ctx.tenant_id, run_id=row["action_run_id"]
        )
        return action_log_payload(row, objects, parameters, len(effects))

    def _revert_response(self, row: ActionRunRow, original_run_id: str) -> dict[str, object]:
        result = dict(row["result"] or {})
        return {"actionRunId": row["id"], "revertOfActionRunId": original_run_id, "status": row["status"], **result}


def _revert_key(original_run_id: str, idempotency_key: str) -> str:
    return f"revert:{original_run_id}:{idempotency_key}"


def _revert_fingerprint(original_run_id: str) -> str:
    return "sha256:" + hashlib.sha256(f"action-revert:{original_run_id}".encode()).hexdigest()


def _monitoring_payload(
    rows: list[ActionRunRow], effect_counts: Mapping[str, int], window_limit: int
) -> dict[str, object]:
    visible = rows[:window_limit]
    durations = [duration for row in visible if (duration := _duration_ms(row)) is not None]
    terminal = [row for row in visible if row["completed_at"] is not None]
    failures = [row for row in terminal if row["status"] in _FAILURE_STATUSES]
    backlog = sum(effect_counts.get(status, 0) for status in ("pending", "delivering", "retry_wait"))
    return {
        "window": {"maxRuns": window_limit, "observedRuns": len(visible), "isTruncated": len(rows) > window_limit},
        "durationMs": {"p95": _nearest_rank_p95(durations), "terminalSample": len(durations)},
        "failure": {"count": len(failures), "rate": round(len(failures) / len(terminal), 4) if terminal else 0.0},
        "effects": {
            "deliveryBacklog": backlog,
            "deadLetter": effect_counts.get("dead_letter", 0),
            "outcomeUnknown": effect_counts.get("outcome_unknown", 0),
        },
    }


def _duration_ms(row: ActionRunRow) -> int | None:
    completed_at = row["completed_at"]
    if completed_at is None:
        return None
    try:
        started = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        completed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, round((completed - started).total_seconds() * 1_000))


def _nearest_rank_p95(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


_FAILURE_STATUSES = {"failed", "conflict", "outcome_unknown", "compensation_required"}
