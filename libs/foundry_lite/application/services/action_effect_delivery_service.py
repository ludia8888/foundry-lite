"""Fenced after-commit Action effect delivery with bounded retry and durable DLQ state."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.action_async_execution_types import (
    ActionAsyncRunRow,
    ActionEffectReceiptRow,
    ActionStepAttemptRow,
)
from foundry_lite.application.ports.action_effect_executor import (
    ActionEffectExecutionResult,
    ActionEffectExecutor,
    ActionEffectPermanentError,
    ActionEffectTransientError,
)
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.services.action_distributed_run_support import stored_action_contract
from foundry_lite.application.services.action_effect_runtime import (
    effect_claim,
    effect_receipt_record,
    effect_request,
    effect_retry_at,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.action_runtime.action_effects import ActionEffectV3
from foundry_lite.domain.context import RequestContext
from foundry_lite.security.tenant_context import tenant_context


class ActionBeforeEffectOutcomeUnknown(RuntimeError):
    """Signal an ambiguous before-commit effect that requires reconciliation."""

    """The pre-commit provider may have accepted the request; automatic replay is unsafe."""


class ActionBeforeEffectFailed(RuntimeError):
    """Signal a known before-commit rejection that prevents Ontology commit."""

    """The pre-commit provider rejected or safely failed the request."""


class ActionEffectDeliveryService(CoreService):
    """Own durable effect receipts, fencing, retries, and delivery evidence."""

    required_dependencies = (
        "engine",
        "metadata_repository",
        "action_execution_repository",
        "action_effect_executor",
    )
    required_collaborators = ("runtime_service",)
    action_effect_executor: ActionEffectExecutor
    runtime_service: RuntimeEvidenceBoundary

    def execute_before(
        self,
        ctx: RequestContext,
        row: ActionAsyncRunRow,
        attempt: ActionStepAttemptRow,
    ) -> dict[str, object] | None:
        """Deliver the optional before-commit effect exactly once or fail closed."""
        effects = tuple(effect for effect in stored_action_contract(row).effects if effect.phase == "before_commit")
        if not effects:
            return None
        effect = effects[0]
        receipt, is_created = self._prepare_before(ctx, row, attempt, effect)
        if not is_created:
            return self._resume_before(ctx, receipt, attempt)
        try:
            result = self.action_effect_executor.execute(effect_request(receipt, ctx.request_id))
        except (ActionEffectTransientError, ActionEffectPermanentError) as exc:
            self._fail_before(ctx, receipt, exc, "dead_letter")
            raise ActionBeforeEffectFailed("before-commit Action effect failed") from exc
        except Exception as exc:  # noqa: BLE001 - unknown transport result is never replayed.
            self._fail_before(ctx, receipt, exc, "outcome_unknown")
            raise ActionBeforeEffectOutcomeUnknown("before-commit Action effect outcome is unknown") from exc
        if result.outcome == "ambiguous":
            self._complete_before(ctx, receipt, "outcome_unknown", dict(result.response), {"kind": "outcome_unknown"})
            raise ActionBeforeEffectOutcomeUnknown("before-commit Action effect outcome is unknown")
        response = {**dict(result.response), "networkEvidence": dict(result.network_evidence)}
        self._complete_before(ctx, receipt, "succeeded", response, None, result.external_execution_id)
        return {"effectId": effect.effect_id, "receiptId": receipt["id"], "response": response}

    def enqueue_after(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        row: ActionAsyncRunRow,
        committed_result: Mapping[str, object],
    ) -> list[str]:
        """Persist after-commit effect receipts and outbox evidence atomically."""
        effects = tuple(effect for effect in stored_action_contract(row).effects if effect.phase == "after_commit")
        return [self._enqueue_one(transaction, ctx, row, effect, committed_result) for effect in effects]

    def deliver_pending(self, *, tenant_id: str, worker_id: str, limit: int = 100) -> dict[str, int]:
        """Claim and deliver due after-commit effects for one tenant."""
        now = _now()
        with self.engine.begin() as transaction:
            rows = self.action_execution_repository.pending_effect_receipts(
                transaction=transaction,
                tenant_id=tenant_id,
                limit=max(1, min(limit, 500)),
                due_at=now,
            )
        counts = {
            "requested": len(rows),
            "succeeded": 0,
            "retry_wait": 0,
            "dead_letter": 0,
            "outcome_unknown": 0,
            "skipped": 0,
        }
        for row in rows:
            status = self._deliver_one(row, worker_id)
            counts[status] += 1
        return counts

    def deliver_all(self, *, worker_id: str, limit: int = 100) -> dict[str, int]:
        """Deliver bounded pending effects across all known tenants."""
        totals = {
            "requested": 0,
            "succeeded": 0,
            "retry_wait": 0,
            "dead_letter": 0,
            "outcome_unknown": 0,
            "skipped": 0,
        }
        for tenant_id in self.metadata_repository.list_tenant_ids():
            with tenant_context(tenant_id):
                result = self.deliver_pending(tenant_id=tenant_id, worker_id=worker_id, limit=limit)
            for key, value in result.items():
                totals[key] += value
        return totals

    def _deliver_one(self, row: ActionEffectReceiptRow, worker_id: str) -> str:
        claimed = self._claim(row, worker_id)
        if claimed is None:
            return "skipped"
        ctx = _worker_context(claimed, worker_id)
        try:
            result = self.action_effect_executor.execute(effect_request(claimed, ctx.request_id))
        except ActionEffectTransientError as exc:
            return self._record_failure(ctx, claimed, exc, is_retryable=True)
        except ActionEffectPermanentError as exc:
            return self._record_failure(ctx, claimed, exc, is_retryable=False)
        except Exception as exc:  # noqa: BLE001 - unknown delivery result is never retried automatically.
            return self._record_ambiguous(ctx, claimed, exc)
        return self._record_result(ctx, claimed, result)

    def _prepare_before(
        self,
        ctx: RequestContext,
        row: ActionAsyncRunRow,
        attempt: ActionStepAttemptRow,
        effect: ActionEffectV3,
    ) -> tuple[ActionEffectReceiptRow, bool]:
        record = effect_receipt_record(ctx, row, effect)
        with self.engine.begin() as transaction:
            inserted = self.action_execution_repository.insert_effect_receipt(transaction=transaction, record=record)
            receipt = inserted or self._receipt(transaction, ctx.tenant_id, row["id"], effect.effect_id)
            if inserted is None:
                return receipt, False
            claimed = self.action_execution_repository.claim_effect_receipt(
                transaction=transaction,
                claim=effect_claim(receipt, attempt["worker_id"], lease_seconds=effect.timeout_seconds + 5),
            )
            if claimed is None:
                raise ActionBeforeEffectOutcomeUnknown("before-commit Action effect could not be fenced")
            return claimed, True

    def _resume_before(
        self, ctx: RequestContext, receipt: ActionEffectReceiptRow, attempt: ActionStepAttemptRow
    ) -> dict[str, object] | None:
        if receipt["status"] == "succeeded":
            return {"effectId": receipt["effect_id"], "receiptId": receipt["id"], "response": receipt["response"] or {}}
        if receipt["status"] == "outcome_unknown":
            raise ActionBeforeEffectOutcomeUnknown("before-commit Action effect is not replayable")
        if receipt["status"] == "dead_letter":
            raise ActionBeforeEffectFailed("before-commit Action effect previously failed")
        with self.engine.begin() as transaction:
            claimed = self.action_execution_repository.claim_effect_receipt(
                transaction=transaction,
                claim=effect_claim(receipt, attempt["worker_id"], is_reconciliation=True),
            )
        if claimed is not None:
            self._complete_before(ctx, claimed, "outcome_unknown", None, {"kind": "worker_lost_after_dispatch"})
        raise ActionBeforeEffectOutcomeUnknown("before-commit Action effect may have been delivered")

    def _fail_before(self, ctx: RequestContext, row: ActionEffectReceiptRow, exc: Exception, status: str) -> None:
        error = dict(self.runtime_service._error_payload(exc, ctx, run_id=row["action_run_id"]))
        self._complete_before(ctx, row, status, None, error)

    def _complete_before(
        self,
        ctx: RequestContext,
        row: ActionEffectReceiptRow,
        status: str,
        response: dict[str, object] | None,
        error: dict[str, object] | None,
        external_execution_id: str | None = None,
    ) -> None:
        with self.engine.begin() as transaction:
            completed = self.action_execution_repository.complete_effect_receipt(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                receipt_id=row["id"],
                worker_id=str(row["worker_id"]),
                lease_token=str(row["lease_token"]),
                fencing_token=row["fencing_token"],
                status=status,
                response=response,
                error=error,
                retry_at=None,
                external_execution_id=external_execution_id,
                completed_at=_now(),
            )
            if completed is None:
                raise ActionBeforeEffectOutcomeUnknown(
                    "before-commit Action effect terminal write lost its fencing lease"
                )
            self._audit(transaction, ctx, completed)

    def _enqueue_one(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        row: ActionAsyncRunRow,
        effect: ActionEffectV3,
        committed_result: Mapping[str, object],
    ) -> str:
        event_id = self.runtime_service._outbox(
            transaction,
            ctx,
            "action.effect.requested",
            "action_run",
            row["id"],
            {"actionRunId": row["id"], "effectId": effect.effect_id, "kind": effect.kind},
            idempotency_key=f"action.effect.requested:{row['id']}:{effect.effect_id}",
            correlation_id=row["id"],
        )
        record = effect_receipt_record(ctx, row, effect, committed_result=committed_result, outbox_event_id=event_id)
        inserted = self.action_execution_repository.insert_effect_receipt(transaction=transaction, record=record)
        receipt = inserted or self._receipt(transaction, ctx.tenant_id, row["id"], effect.effect_id)
        return receipt["id"]

    def _receipt(
        self, transaction: TransactionContext, tenant_id: str, run_id: str, effect_id: str
    ) -> ActionEffectReceiptRow:
        receipts = self.action_execution_repository.effect_receipts_for_run(
            transaction=transaction, tenant_id=tenant_id, run_id=run_id
        )
        return next(receipt for receipt in receipts if receipt["effect_id"] == effect_id)

    def _claim(self, row: ActionEffectReceiptRow, worker_id: str) -> ActionEffectReceiptRow | None:
        with self.engine.begin() as transaction:
            return self.action_execution_repository.claim_effect_receipt(
                transaction=transaction, claim=effect_claim(row, worker_id)
            )

    def _record_result(
        self, ctx: RequestContext, row: ActionEffectReceiptRow, result: ActionEffectExecutionResult
    ) -> str:
        if result.outcome == "ambiguous":
            return self._complete(ctx, row, "outcome_unknown", dict(result.response), {"kind": "outcome_unknown"}, None)
        response = {**dict(result.response), "networkEvidence": dict(result.network_evidence)}
        return self._complete(ctx, row, "succeeded", response, None, result.external_execution_id)

    def _record_failure(
        self, ctx: RequestContext, row: ActionEffectReceiptRow, exc: Exception, *, is_retryable: bool
    ) -> str:
        can_retry = is_retryable and row["attempt_count"] < row["max_attempts"]
        status = "retry_wait" if can_retry else "dead_letter"
        error = dict(self.runtime_service._error_payload(exc, ctx, run_id=row["action_run_id"]))
        return self._complete(
            ctx, row, status, None, error, None, retry_at=effect_retry_at(row["attempt_count"]) if can_retry else None
        )

    def _record_ambiguous(self, ctx: RequestContext, row: ActionEffectReceiptRow, exc: Exception) -> str:
        error = dict(self.runtime_service._error_payload(exc, ctx, run_id=row["action_run_id"]))
        error["kind"] = "outcome_unknown"
        return self._complete(ctx, row, "outcome_unknown", None, error, None)

    def _complete(
        self,
        ctx: RequestContext,
        row: ActionEffectReceiptRow,
        status: str,
        response: dict[str, object] | None,
        error: dict[str, object] | None,
        external_execution_id: str | None,
        *,
        retry_at: str | None = None,
    ) -> str:
        with self.engine.begin() as transaction:
            completed = self.action_execution_repository.complete_effect_receipt(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                receipt_id=row["id"],
                worker_id=str(row["worker_id"]),
                lease_token=str(row["lease_token"]),
                fencing_token=row["fencing_token"],
                status=status,
                response=response,
                error=error,
                retry_at=retry_at,
                external_execution_id=external_execution_id,
                completed_at=_now(),
            )
            if completed is None:
                return "skipped"
            self._audit(transaction, ctx, completed)
        return status

    def _audit(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        row: ActionEffectReceiptRow,
    ) -> None:
        self.runtime_service._audit(
            transaction,
            ctx,
            event_type=f"action.effect.{row['status']}",
            resource_type="action_effect_receipt",
            resource_id=row["id"],
            action="deliver",
            decision="allow" if row["status"] == "succeeded" else "deny",
            after_ref={"status": row["status"], "effectId": row["effect_id"], "retryAt": row["retry_at"]},
            correlation_id=row["action_run_id"],
        )


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _worker_context(row: ActionEffectReceiptRow, worker_id: str) -> RequestContext:
    return RequestContext(
        tenant_id=row["tenant_id"],
        actor_user_id=worker_id,
        request_id=f"action-effect:{row['id']}:{row['attempt_count']}",
        roles=("admin",),
    )
