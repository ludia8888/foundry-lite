"""Persistence port for durable Action run, step, attempt, and event evidence."""

from __future__ import annotations

from typing import Protocol

from foundry_lite.application.action_async_execution_types import (
    ActionAsyncRunRecord,
    ActionAsyncRunRow,
    ActionEffectClaim,
    ActionEffectOperationRecord,
    ActionEffectOperationRow,
    ActionEffectReceiptRecord,
    ActionEffectReceiptRow,
    ActionRunEventRecord,
    ActionRunEventRow,
    ActionRunStepRecord,
    ActionRunStepRow,
    ActionStepAttemptClaim,
    ActionStepAttemptRow,
    JsonObject,
)
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.application.state_transitions import StatusTransition


class ActionExecutionRepository(Protocol):
    def insert_run(
        self,
        *,
        transaction: TransactionContext,
        record: ActionAsyncRunRecord,
        steps: tuple[ActionRunStepRecord, ...],
    ) -> ActionAsyncRunRow | None: ...

    def run_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, run_id: str
    ) -> ActionAsyncRunRow | None: ...

    def run_by_idempotency_key(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        action_type_id: str,
        actor_user_id: str,
        idempotency_key: str,
    ) -> ActionAsyncRunRow | None: ...

    def list_runs(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        before_created_at: str | None,
        before_run_id: str | None,
        limit: int,
    ) -> list[ActionAsyncRunRow]: ...

    def steps_for_run(
        self, *, transaction: TransactionContext, tenant_id: str, run_id: str
    ) -> list[ActionRunStepRow]: ...

    def attempts_for_run(
        self, *, transaction: TransactionContext, tenant_id: str, run_id: str
    ) -> list[ActionStepAttemptRow]: ...

    def insert_effect_receipt(
        self, *, transaction: TransactionContext, record: ActionEffectReceiptRecord
    ) -> ActionEffectReceiptRow | None: ...

    def effect_receipts_for_run(
        self, *, transaction: TransactionContext, tenant_id: str, run_id: str
    ) -> list[ActionEffectReceiptRow]: ...

    def effect_receipt_by_id(
        self, *, transaction: TransactionContext, tenant_id: str, receipt_id: str
    ) -> ActionEffectReceiptRow | None: ...

    def list_effect_receipts(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        status: str | None,
        before_created_at: str | None,
        before_receipt_id: str | None,
        limit: int,
    ) -> list[ActionEffectReceiptRow]: ...

    def pending_effect_receipts(
        self, *, transaction: TransactionContext, tenant_id: str, limit: int, due_at: str
    ) -> list[ActionEffectReceiptRow]: ...

    def effect_status_counts(self, *, transaction: TransactionContext, tenant_id: str) -> dict[str, int]: ...

    def claim_effect_receipt(
        self, *, transaction: TransactionContext, claim: ActionEffectClaim
    ) -> ActionEffectReceiptRow | None: ...

    def start_effect_dispatch(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        receipt_id: str,
        worker_id: str,
        lease_token: str,
        fencing_token: int,
        started_at: str,
    ) -> ActionEffectReceiptRow | None: ...

    def request_effect_cancel(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        receipt_id: str,
        reason: str | None,
        requested_at: str,
    ) -> ActionEffectReceiptRow | None: ...

    def retry_effect_receipt(
        self, *, transaction: TransactionContext, tenant_id: str, receipt_id: str, requested_at: str
    ) -> ActionEffectReceiptRow | None: ...

    def reconcile_effect_receipt(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        receipt_id: str,
        resolution: str,
        evidence: JsonObject,
        actor_user_id: str,
        reconciled_at: str,
    ) -> ActionEffectReceiptRow | None: ...

    def effect_operation_by_idempotency(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        actor_user_id: str,
        operation: str,
        idempotency_key: str,
    ) -> ActionEffectOperationRow | None: ...

    def insert_effect_operation_or_existing(
        self, *, transaction: TransactionContext, record: ActionEffectOperationRecord
    ) -> ActionEffectOperationRow | None: ...

    def complete_effect_receipt(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        receipt_id: str,
        worker_id: str,
        lease_token: str,
        fencing_token: int,
        status: str,
        response: JsonObject | None,
        error: JsonObject | None,
        retry_at: str | None,
        external_execution_id: str | None,
        completed_at: str,
    ) -> ActionEffectReceiptRow | None: ...

    def update_dispatch(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
        workflow_run_id: str,
        dispatch_status: str,
        dispatch_error: JsonObject | None,
    ) -> ActionAsyncRunRow | None: ...

    def pending_dispatches(
        self, *, transaction: TransactionContext, tenant_id: str, limit: int
    ) -> list[ActionAsyncRunRow]: ...

    def cancelling_runs(
        self, *, transaction: TransactionContext, tenant_id: str, limit: int
    ) -> list[ActionAsyncRunRow]: ...

    def transition_run(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
        transition: StatusTransition,
        changed_at: str,
        error: JsonObject | None = None,
        result: JsonObject | None = None,
    ) -> ActionAsyncRunRow | None: ...

    def request_cancel(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        reason: str | None,
        requested_at: str,
    ) -> ActionAsyncRunRow | None: ...

    def claim_step(
        self, *, transaction: TransactionContext, claim: ActionStepAttemptClaim
    ) -> ActionStepAttemptRow | None: ...

    def heartbeat_attempt(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        attempt_id: str,
        worker_id: str,
        lease_token: str,
        fencing_token: int,
        lease_expires_at: str,
        heartbeat_at: str,
    ) -> ActionStepAttemptRow | None: ...

    def lock_attempt_owner(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        attempt_id: str,
        worker_id: str,
        lease_token: str,
        fencing_token: int,
        owned_at: str,
    ) -> ActionStepAttemptRow | None: ...

    def complete_attempt(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        attempt_id: str,
        worker_id: str,
        lease_token: str,
        fencing_token: int,
        status: str,
        output_manifest: JsonObject,
        error: JsonObject | None,
        error_kind: str | None,
        completed_at: str,
        retry_at: str | None = None,
        external_execution_id: str | None = None,
    ) -> ActionStepAttemptRow | None: ...

    def append_event(self, *, transaction: TransactionContext, record: ActionRunEventRecord) -> ActionRunEventRow: ...

    def run_events(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        run_id: str,
        after_sequence: int,
        limit: int,
    ) -> list[ActionRunEventRow]: ...
