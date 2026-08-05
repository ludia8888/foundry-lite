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
    ActionNotificationRecipientDirectory,
    AuthorizedNotificationRequest,
    audit_effect_receipt,
    authorize_notification_request,
    deliver_effect_rows,
    effect_claim,
    effect_now,
    effect_receipt_record,
    effect_request,
    effect_retry_at,
    effect_worker_context,
)
from foundry_lite.application.services.action_notification_rendering import (
    PreparedActionEffect,
    prepare_after_effect,
)
from foundry_lite.application.services.action_protocols import ActionObjectRecordLookup, ActionOntologyLookup
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.action_runtime.action_effects import ActionEffectV3, validate_action_effect_response
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.security.tenant_context import tenant_context


class ActionBeforeEffectOutcomeUnknown(RuntimeError):
    """Signal an ambiguous before-commit effect that requires reconciliation."""


class ActionBeforeEffectFailed(RuntimeError):
    """Signal a known before-commit rejection that prevents Ontology commit."""


class ActionEffectDeliveryService(CoreService):
    """Own durable effect receipts, fencing, retries, and delivery evidence."""

    required_dependencies = (
        "engine",
        "policy",
        "metadata_repository",
        "action_execution_repository",
        "action_effect_executor",
        "action_notification_recipient_directory",
    )
    required_collaborators = ("runtime_service", "object_records_service", "ontology_lookup_service")
    action_effect_executor: ActionEffectExecutor
    action_notification_recipient_directory: ActionNotificationRecipientDirectory
    runtime_service: RuntimeEvidenceBoundary
    object_records_service: ActionObjectRecordLookup
    ontology_lookup_service: ActionOntologyLookup

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
        return self._dispatch_before(ctx, receipt)

    def _dispatch_before(
        self,
        ctx: RequestContext,
        receipt: ActionEffectReceiptRow,
    ) -> dict[str, object] | None:
        dispatched = self._start_dispatch(ctx, receipt)
        if dispatched is None:
            raise ActionBeforeEffectOutcomeUnknown("before-commit Action effect lost its dispatch fence")
        request = effect_request(dispatched, ctx.request_id)
        try:
            result = self.action_effect_executor.execute(request)
        except (ActionEffectTransientError, ActionEffectPermanentError) as exc:
            self._fail_before(ctx, dispatched, exc, "dead_letter")
            raise ActionBeforeEffectFailed("before-commit Action effect failed") from exc
        except Exception as exc:  # noqa: BLE001 - unknown transport result is never replayed.
            self._fail_before(ctx, dispatched, exc, "outcome_unknown")
            raise ActionBeforeEffectOutcomeUnknown("before-commit Action effect outcome is unknown") from exc
        if result.outcome == "ambiguous":
            self._complete_before(
                ctx,
                dispatched,
                "outcome_unknown",
                dict(result.response),
                {"kind": "outcome_unknown"},
            )
            raise ActionBeforeEffectOutcomeUnknown("before-commit Action effect outcome is unknown")
        try:
            validate_action_effect_response(request.effect, result.response)
        except ValidationFailed as exc:
            self._fail_before(ctx, dispatched, exc, "outcome_unknown")
            raise ActionBeforeEffectOutcomeUnknown("before-commit Action effect response is invalid") from exc
        response = {**dict(result.response), "networkEvidence": dict(result.network_evidence)}
        self._complete_before(ctx, dispatched, "succeeded", response, None, result.external_execution_id)
        return {"effectId": request.effect.effect_id, "receiptId": dispatched["id"], "response": response}

    def enqueue_after(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        row: ActionAsyncRunRow,
        committed_result: Mapping[str, object],
        prepared_effects: tuple[PreparedActionEffect, ...],
    ) -> list[str]:
        """Persist after-commit effect receipts and outbox evidence atomically."""
        return [self._enqueue_one(transaction, ctx, row, prepared, committed_result) for prepared in prepared_effects]

    def prepare_after(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        row: ActionAsyncRunRow,
    ) -> tuple[PreparedActionEffect, ...]:
        """Freeze every notification payload while the target still has pre-edit values."""
        effects = tuple(effect for effect in stored_action_contract(row).effects if effect.phase == "after_commit")
        record = self.object_records_service._object_record(
            transaction,
            ctx,
            row["target_object_type_api_name"],
            row["target_object_id"],
            row["target_object_type_id"],
        )
        properties = record["properties"] if record is not None else {}
        object_version = record["object_version"] if record is not None else None
        return tuple(
            prepare_after_effect(
                effect,
                object_properties=properties,
                object_type=row["target_object_type_api_name"],
                object_id=row["target_object_id"],
                object_version=object_version,
                parameters=row["parameters"],
                actor_user_id=row["actor_user_id"],
                action_run_id=row["id"],
                action_api_name=row["action_type_api_name"],
            )
            for effect in effects
        )

    def deliver_pending(
        self,
        *,
        tenant_id: str,
        worker_id: str,
        limit: int = 100,
        lease_seconds: int = 30,
        concurrency: int = 4,
    ) -> dict[str, int]:
        """Claim and deliver due after-commit effects for one tenant."""
        now = effect_now()
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
            "cancelled": 0,
            "skipped": 0,
        }
        for status in deliver_effect_rows(rows, worker_id, lease_seconds, concurrency, self._deliver_one):
            counts[status] += 1
        return counts

    def deliver_all(
        self,
        *,
        worker_id: str,
        limit: int = 100,
        lease_seconds: int = 30,
        concurrency: int = 4,
    ) -> dict[str, int]:
        """Deliver bounded pending effects across all known tenants."""
        totals = {
            "requested": 0,
            "succeeded": 0,
            "retry_wait": 0,
            "dead_letter": 0,
            "outcome_unknown": 0,
            "cancelled": 0,
            "skipped": 0,
        }
        for tenant_id in self.metadata_repository.list_tenant_ids():
            with tenant_context(tenant_id):
                result = self.deliver_pending(
                    tenant_id=tenant_id,
                    worker_id=worker_id,
                    limit=limit,
                    lease_seconds=lease_seconds,
                    concurrency=concurrency,
                )
            for key, value in result.items():
                totals[key] += value
        return totals

    def _deliver_one(self, row: ActionEffectReceiptRow, worker_id: str, lease_seconds: int) -> str:
        claimed = self._claim(row, worker_id, lease_seconds)
        if claimed is None:
            return "skipped"
        ctx = effect_worker_context(claimed, worker_id)
        if claimed["dispatch_started_at"] is not None:
            return self._record_ambiguous(
                ctx,
                claimed,
                RuntimeError("previous worker lease expired after external dispatch started"),
            )
        try:
            authorized = self._authorized_request(ctx, claimed)
            if authorized.is_delivery_suppressed:
                return self._record_suppressed(ctx, claimed, authorized)
            dispatched = self._start_dispatch(ctx, claimed)
            if dispatched is None:
                return self._status_after_dispatch_denial(ctx, claimed)
            result = self.action_effect_executor.execute(authorized.request)
        except ActionEffectTransientError as exc:
            return self._record_failure(ctx, claimed, exc, is_retryable=True)
        except ActionEffectPermanentError as exc:
            return self._record_failure(ctx, claimed, exc, is_retryable=False)
        except Exception as exc:  # noqa: BLE001 - unknown delivery result is never retried automatically.
            return self._record_ambiguous(ctx, claimed, exc)
        return self._record_result(ctx, claimed, result, authorized.evidence)

    def _start_dispatch(self, ctx: RequestContext, row: ActionEffectReceiptRow) -> ActionEffectReceiptRow | None:
        with self.engine.begin() as transaction:
            return self.action_execution_repository.start_effect_dispatch(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                receipt_id=row["id"],
                worker_id=str(row["worker_id"]),
                lease_token=str(row["lease_token"]),
                fencing_token=row["fencing_token"],
                started_at=effect_now(),
            )

    def _status_after_dispatch_denial(self, ctx: RequestContext, row: ActionEffectReceiptRow) -> str:
        current = self._current_receipt(ctx, row["id"])
        return "cancelled" if current is not None and current["status"] == "cancelled" else "skipped"

    def _current_receipt(self, ctx: RequestContext, receipt_id: str) -> ActionEffectReceiptRow | None:
        with self.engine.begin() as transaction:
            return self.action_execution_repository.effect_receipt_by_id(
                transaction=transaction,
                tenant_id=ctx.tenant_id,
                receipt_id=receipt_id,
            )

    def _authorized_request(self, ctx: RequestContext, row: ActionEffectReceiptRow) -> AuthorizedNotificationRequest:
        request = effect_request(row, ctx.request_id)
        with self.engine.begin() as transaction:
            return authorize_notification_request(
                transaction,
                self.policy,
                self.action_notification_recipient_directory,
                self.object_records_service,
                self.ontology_lookup_service,
                request,
            )

    def _record_suppressed(
        self,
        ctx: RequestContext,
        row: ActionEffectReceiptRow,
        authorized: AuthorizedNotificationRequest,
    ) -> str:
        response = {"deliverySuppressed": True, "recipientAuthorization": dict(authorized.evidence)}
        return self._complete(ctx, row, "succeeded", response, None, None)

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
            if receipt["dispatch_started_at"] is None:
                return self._dispatch_before(ctx, claimed)
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
                completed_at=effect_now(),
            )
            if completed is None:
                raise ActionBeforeEffectOutcomeUnknown(
                    "before-commit Action effect terminal write lost its fencing lease"
                )
            audit_effect_receipt(self.runtime_service, transaction, ctx, completed)

    def _enqueue_one(
        self,
        transaction: TransactionContext,
        ctx: RequestContext,
        row: ActionAsyncRunRow,
        prepared: PreparedActionEffect,
        committed_result: Mapping[str, object],
    ) -> str:
        effect = prepared.effect
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
        record = effect_receipt_record(
            ctx,
            row,
            effect,
            committed_result=committed_result,
            outbox_event_id=event_id,
            rendering_evidence=prepared.rendering_evidence,
        )
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

    def _claim(self, row: ActionEffectReceiptRow, worker_id: str, lease_seconds: int) -> ActionEffectReceiptRow | None:
        with self.engine.begin() as transaction:
            return self.action_execution_repository.claim_effect_receipt(
                transaction=transaction,
                claim=effect_claim(row, worker_id, lease_seconds=max(1, min(lease_seconds, 3600))),
            )

    def _record_result(
        self,
        ctx: RequestContext,
        row: ActionEffectReceiptRow,
        result: ActionEffectExecutionResult,
        recipient_authorization: Mapping[str, object],
    ) -> str:
        if result.outcome == "ambiguous":
            return self._complete(ctx, row, "outcome_unknown", dict(result.response), {"kind": "outcome_unknown"}, None)
        response = {**dict(result.response), "networkEvidence": dict(result.network_evidence)}
        if recipient_authorization:
            response["recipientAuthorization"] = dict(recipient_authorization)
        current = self._current_receipt(ctx, row["id"])
        if current is not None and current["cancel_requested_at"] is not None:
            response["cancellationRace"] = {
                "requestedAt": current["cancel_requested_at"],
                "disposition": "remote_delivery_won",
            }
        return self._complete(ctx, row, "succeeded", response, None, result.external_execution_id)

    def _record_failure(
        self, ctx: RequestContext, row: ActionEffectReceiptRow, exc: Exception, *, is_retryable: bool
    ) -> str:
        current = self._current_receipt(ctx, row["id"])
        if current is not None and current["cancel_requested_at"] is not None:
            error = dict(self.runtime_service._error_payload(exc, ctx, run_id=row["action_run_id"]))
            error["kind"] = "cancelled_after_known_safe_failure"
            return self._complete(ctx, row, "cancelled", None, error, None)
        can_retry = is_retryable and row["attempt_count"] < row["max_attempts"]
        status = "retry_wait" if can_retry else "dead_letter"
        error = dict(self.runtime_service._error_payload(exc, ctx, run_id=row["action_run_id"]))
        return self._complete(
            ctx, row, status, None, error, None, retry_at=effect_retry_at(row["attempt_count"]) if can_retry else None
        )

    def _record_ambiguous(self, ctx: RequestContext, row: ActionEffectReceiptRow, exc: Exception) -> str:
        error = dict(self.runtime_service._error_payload(exc, ctx, run_id=row["action_run_id"]))
        error["kind"] = "outcome_unknown"
        current = self._current_receipt(ctx, row["id"])
        if current is not None and current["cancel_requested_at"] is not None:
            error["cancellationRequestedAt"] = current["cancel_requested_at"]
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
                completed_at=effect_now(),
            )
            if completed is None:
                return "skipped"
            audit_effect_receipt(self.runtime_service, transaction, ctx, completed)
        return status
