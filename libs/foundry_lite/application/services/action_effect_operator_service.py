"""Operator queue, cancellation, retry, and reconciliation for Action effects."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.action_async_execution_types import (
    ActionEffectOperationRecord,
    ActionEffectReceiptRow,
)
from foundry_lite.application.ports.action_execution_repository import ActionExecutionRepository
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.action_distributed_run_evidence import append_action_run_event
from foundry_lite.application.services.action_effect_operator_payloads import (
    decode_effect_cursor,
    effect_operation_fingerprint,
    effect_operator_view,
    encode_effect_cursor,
    normalize_effect_status,
    normalize_reconciliation,
    require_effect_operation_key,
    require_effect_operation_replay,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed


class ActionEffectOperatorService(CoreService):
    """Expose fail-closed human controls over durable after-commit receipts."""

    required_dependencies = ("engine", "policy", "action_execution_repository")
    required_collaborators = ("runtime_service",)
    action_execution_repository: ActionExecutionRepository
    runtime_service: RuntimeEvidenceBoundary

    def list_receipts(
        self,
        *,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        request_context = self._require_read(ctx)
        before_created_at, before_receipt_id = decode_effect_cursor(cursor, request_context.tenant_id)
        bounded = min(max(limit, 1), 100)
        with self.engine.begin() as transaction:
            rows = self.action_execution_repository.list_effect_receipts(
                transaction=transaction,
                tenant_id=request_context.tenant_id,
                status=normalize_effect_status(status),
                before_created_at=before_created_at,
                before_receipt_id=before_receipt_id,
                limit=bounded + 1,
            )
        visible = rows[:bounded]
        next_cursor = encode_effect_cursor(request_context.tenant_id, visible[-1]) if len(rows) > bounded else None
        return {"items": [effect_operator_view(row) for row in visible], "nextCursor": next_cursor}

    def get_receipt(self, receipt_id: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        request_context = self._require_read(ctx)
        with self.engine.begin() as transaction:
            return effect_operator_view(self._require_receipt(transaction, request_context, receipt_id))

    def cancel(
        self,
        receipt_id: str,
        *,
        reason: str | None,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        request_context = self._require_manage(ctx, "cancel", receipt_id)
        normalized_reason = self._reason(reason)
        request = {"reason": normalized_reason}
        return self._cancel(
            request_context,
            receipt_id,
            normalized_reason,
            require_effect_operation_key(idempotency_key),
            effect_operation_fingerprint("cancel", receipt_id, request),
        )

    def retry(
        self,
        receipt_id: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        request_context = self._require_manage(ctx, "retry", receipt_id)
        key = require_effect_operation_key(idempotency_key)
        fingerprint = effect_operation_fingerprint("retry", receipt_id, {})
        with self.engine.begin() as transaction:
            replay = self._replay(transaction, request_context, "retry", key, fingerprint)
            if replay is not None:
                return replay
            current = self._require_receipt(transaction, request_context, receipt_id)
            self._require_retryable(current)
            updated = self.action_execution_repository.retry_effect_receipt(
                transaction=transaction,
                tenant_id=request_context.tenant_id,
                receipt_id=receipt_id,
                requested_at=_now(),
            )
            return self._finish(transaction, request_context, "retry", key, fingerprint, updated, current)

    def reconcile(
        self,
        receipt_id: str,
        *,
        resolution: str,
        evidence: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        request_context = self._require_manage(ctx, "reconcile", receipt_id)
        normalized_resolution, normalized_evidence = normalize_reconciliation(resolution, evidence)
        request = {"resolution": normalized_resolution, "evidence": normalized_evidence}
        key = require_effect_operation_key(idempotency_key)
        fingerprint = effect_operation_fingerprint("reconcile", receipt_id, request)
        return self._reconcile(
            request_context,
            receipt_id,
            normalized_resolution,
            normalized_evidence,
            key,
            fingerprint,
        )

    def _cancel(
        self,
        ctx: RequestContext,
        receipt_id: str,
        reason: str | None,
        key: str,
        fingerprint: str,
    ) -> dict[str, object]:
        with self.engine.begin() as transaction:
            replay = self._replay(transaction, ctx, "cancel", key, fingerprint)
            if replay is not None:
                return replay
            current = self._require_receipt(transaction, ctx, receipt_id)
            self._require_cancellable(current)
            updated = self.action_execution_repository.request_effect_cancel(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                receipt_id=receipt_id,
                reason=reason,
                requested_at=_now(),
            )
            if updated is not None and updated["status"] != "cancelled" and updated["cancel_requested_at"] is None:
                raise ConflictDetected("Action effect became terminal before cancellation was recorded")
            return self._finish(transaction, ctx, "cancel", key, fingerprint, updated, current)

    def _reconcile(
        self,
        ctx: RequestContext,
        receipt_id: str,
        resolution: str,
        evidence: dict[str, object],
        key: str,
        fingerprint: str,
    ) -> dict[str, object]:
        with self.engine.begin() as transaction:
            replay = self._replay(transaction, ctx, "reconcile", key, fingerprint)
            if replay is not None:
                return replay
            current = self._require_receipt(transaction, ctx, receipt_id)
            if current["status"] != "outcome_unknown":
                raise ConflictDetected("only outcome_unknown effects may be reconciled")
            updated = self.action_execution_repository.reconcile_effect_receipt(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                receipt_id=receipt_id,
                resolution=resolution,
                evidence=evidence,
                actor_user_id=ctx.actor_user_id,
                reconciled_at=_now(),
            )
            return self._finish(transaction, ctx, "reconcile", key, fingerprint, updated, current)

    def _finish(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        operation: str,
        key: str,
        fingerprint: str,
        updated: ActionEffectReceiptRow | None,
        before: ActionEffectReceiptRow,
    ) -> dict[str, object]:
        if updated is None:
            raise ConflictDetected("Action effect changed concurrently")
        response = effect_operator_view(updated)
        self._record_evidence(transaction, ctx, operation, key, before, updated)
        self._store_operation(transaction, ctx, operation, key, fingerprint, updated["id"], response)
        return response

    def _record_evidence(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        operation: str,
        key: str,
        before: ActionEffectReceiptRow,
        after: ActionEffectReceiptRow,
    ) -> None:
        event_type = f"action.effect.operator_{operation}"
        evidence = self._audit_view(after)
        append_action_run_event(
            self.action_execution_repository,
            transaction,
            ctx,
            after["action_run_id"],
            event_type,
            evidence,
        )
        self.runtime_service._audit(
            transaction,
            ctx,
            event_type=event_type,
            resource_type="action_effect_receipt",
            resource_id=after["id"],
            action=operation,
            before_ref=self._audit_view(before),
            after_ref=evidence,
            correlation_id=key,
        )
        self.runtime_service._outbox(
            transaction,
            ctx,
            event_type,
            "action_effect_receipt",
            after["id"],
            evidence,
            idempotency_key=key,
            correlation_id=after["action_run_id"],
        )

    def _store_operation(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        operation: str,
        key: str,
        fingerprint: str,
        receipt_id: str,
        response: dict[str, object],
    ) -> None:
        existing = self.action_execution_repository.insert_effect_operation_or_existing(
            transaction=transaction,
            record=ActionEffectOperationRecord(
                _new_id("action-effect-operation"),
                ctx.tenant_id,
                ctx.actor_user_id,
                receipt_id,
                operation,
                key,
                fingerprint,
                response,
                _now(),
            ),
        )
        if existing is not None:
            require_effect_operation_replay(existing, fingerprint)

    def _replay(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        operation: str,
        key: str,
        fingerprint: str,
    ) -> dict[str, object] | None:
        row = self.action_execution_repository.effect_operation_by_idempotency(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.actor_user_id,
            operation=operation,
            idempotency_key=key,
        )
        return require_effect_operation_replay(row, fingerprint) if row else None

    def _require_receipt(
        self, transaction: TransactionContext, ctx: RequestContext, receipt_id: str
    ) -> ActionEffectReceiptRow:
        row = self.action_execution_repository.effect_receipt_by_id(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            receipt_id=receipt_id,
        )
        if row is None:
            raise NotFound("Action effect receipt not found")
        return row

    @staticmethod
    def _require_cancellable(row: ActionEffectReceiptRow) -> None:
        if row["phase"] != "after_commit":
            raise ConflictDetected("before-commit effects are controlled by the owning Action run")
        if row["status"] not in {"pending", "retry_wait", "delivering"}:
            raise ConflictDetected("Action effect is already terminal")

    @staticmethod
    def _require_retryable(row: ActionEffectReceiptRow) -> None:
        if row["phase"] != "after_commit" or row["status"] != "dead_letter":
            raise ConflictDetected("only after-commit dead-letter effects may be retried")

    @staticmethod
    def _reason(value: str | None) -> str | None:
        if value is None:
            return None
        reason = value.strip()
        if not reason or len(reason) > 500:
            raise ValidationFailed("effect cancellation reason must contain 1..500 characters")
        return reason

    @staticmethod
    def _audit_view(row: ActionEffectReceiptRow) -> dict[str, object]:
        return {
            "actionRunId": row["action_run_id"],
            "effectId": row["effect_id"],
            "status": row["status"],
            "attemptCount": row["attempt_count"],
            "fencingToken": row["fencing_token"],
            "cancellationDisposition": effect_operator_view(row)["cancellationDisposition"],
            "isReconciled": row["reconciled_at"] is not None,
        }

    def _require_read(self, ctx: RequestContext | None) -> RequestContext:
        request_context = ctx or RequestContext()
        self.policy.require(request_context, "action:effect:read")
        return request_context

    def _require_manage(self, ctx: RequestContext | None, operation: str, receipt_id: str) -> RequestContext:
        request_context = ctx or RequestContext()
        self.policy.require(request_context, "action:effect:manage")
        self.runtime_service._require_write_traffic_open(
            request_context,
            operation=f"{operation}_action_effect",
            resource_type="action_effect_receipt",
            resource_id=receipt_id,
        )
        return request_context
