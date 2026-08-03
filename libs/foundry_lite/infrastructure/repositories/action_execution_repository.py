"""SQLAlchemy adapter for durable Action execution evidence."""

from __future__ import annotations

from typing import Any

from sqlalchemy.engine import Engine

import foundry_lite.infrastructure.repositories.action_effect_receipt_rows as effect_rows
import foundry_lite.infrastructure.repositories.action_execution_attempt_rows as attempt_rows
import foundry_lite.infrastructure.repositories.action_execution_event_rows as event_rows
import foundry_lite.infrastructure.repositories.action_execution_run_rows as run_rows
from foundry_lite.application.action_async_execution_types import (
    ActionAsyncRunRecord,
    ActionAsyncRunRow,
    ActionEffectClaim,
    ActionEffectReceiptRecord,
    ActionEffectReceiptRow,
    ActionRunEventRecord,
    ActionRunEventRow,
    ActionRunStepRecord,
    ActionRunStepRow,
    ActionStepAttemptClaim,
    ActionStepAttemptRow,
)
from foundry_lite.application.state_transitions import StatusTransition


class SqlAlchemyActionExecutionRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def insert_run(
        self, *, transaction: Any, record: ActionAsyncRunRecord, steps: tuple[ActionRunStepRecord, ...]
    ) -> ActionAsyncRunRow | None:
        return run_rows.insert_run(transaction, record, steps)

    def run_by_id(self, *, transaction: Any, tenant_id: str, run_id: str) -> ActionAsyncRunRow | None:
        return run_rows.run_by_id(transaction, tenant_id, run_id)

    def run_by_idempotency_key(
        self, *, transaction: Any, tenant_id: str, action_type_id: str, actor_user_id: str, idempotency_key: str
    ) -> ActionAsyncRunRow | None:
        return run_rows.run_by_idempotency_key(transaction, tenant_id, action_type_id, actor_user_id, idempotency_key)

    def list_runs(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        before_created_at: str | None,
        before_run_id: str | None,
        limit: int,
    ) -> list[ActionAsyncRunRow]:
        return run_rows.list_runs(transaction, tenant_id, before_created_at, before_run_id, limit)

    def steps_for_run(self, *, transaction: Any, tenant_id: str, run_id: str) -> list[ActionRunStepRow]:
        return run_rows.steps_for_run(transaction, tenant_id, run_id)

    def attempts_for_run(self, *, transaction: Any, tenant_id: str, run_id: str) -> list[ActionStepAttemptRow]:
        return attempt_rows.attempts_for_run(transaction, tenant_id, run_id)

    def insert_effect_receipt(
        self, *, transaction: Any, record: ActionEffectReceiptRecord
    ) -> ActionEffectReceiptRow | None:
        return effect_rows.insert_receipt(transaction, record)

    def effect_receipts_for_run(self, *, transaction: Any, tenant_id: str, run_id: str) -> list[ActionEffectReceiptRow]:
        return effect_rows.receipts_for_run(transaction, tenant_id, run_id)

    def pending_effect_receipts(
        self, *, transaction: Any, tenant_id: str, limit: int, due_at: str
    ) -> list[ActionEffectReceiptRow]:
        return effect_rows.pending_receipts(transaction, tenant_id, limit, due_at)

    def claim_effect_receipt(self, *, transaction: Any, claim: ActionEffectClaim) -> ActionEffectReceiptRow | None:
        return effect_rows.claim_receipt(transaction, claim)

    def complete_effect_receipt(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        receipt_id: str,
        worker_id: str,
        lease_token: str,
        fencing_token: int,
        status: str,
        response: dict[str, object] | None,
        error: dict[str, object] | None,
        retry_at: str | None,
        external_execution_id: str | None,
        completed_at: str,
    ) -> ActionEffectReceiptRow | None:
        return effect_rows.complete_receipt(
            transaction,
            tenant_id=tenant_id,
            receipt_id=receipt_id,
            worker_id=worker_id,
            lease_token=lease_token,
            fencing_token=fencing_token,
            status=status,
            response=response,
            error=error,
            retry_at=retry_at,
            external_execution_id=external_execution_id,
            completed_at=completed_at,
        )

    def update_dispatch(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        run_id: str,
        workflow_run_id: str,
        dispatch_status: str,
        dispatch_error: dict[str, object] | None,
    ) -> ActionAsyncRunRow | None:
        return run_rows.update_dispatch(
            transaction,
            tenant_id=tenant_id,
            run_id=run_id,
            workflow_run_id=workflow_run_id,
            dispatch_status=dispatch_status,
            dispatch_error=dispatch_error,
        )

    def pending_dispatches(self, *, transaction: Any, tenant_id: str, limit: int) -> list[ActionAsyncRunRow]:
        return run_rows.pending_dispatches(transaction, tenant_id, limit)

    def cancelling_runs(self, *, transaction: Any, tenant_id: str, limit: int) -> list[ActionAsyncRunRow]:
        return run_rows.cancelling_runs(transaction, tenant_id, limit)

    def transition_run(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        run_id: str,
        transition: StatusTransition,
        changed_at: str,
        error: dict[str, object] | None = None,
        result: dict[str, object] | None = None,
    ) -> ActionAsyncRunRow | None:
        return run_rows.transition_run(
            transaction,
            tenant_id=tenant_id,
            run_id=run_id,
            transition=transition,
            changed_at=changed_at,
            error=error,
            result=result,
        )

    def request_cancel(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        run_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        reason: str | None,
        requested_at: str,
    ) -> ActionAsyncRunRow | None:
        return event_rows.request_cancel(
            transaction,
            tenant_id=tenant_id,
            run_id=run_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            reason=reason,
            requested_at=requested_at,
        )

    def claim_step(self, *, transaction: Any, claim: ActionStepAttemptClaim) -> ActionStepAttemptRow | None:
        return attempt_rows.claim_step(transaction, claim)

    def heartbeat_attempt(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        attempt_id: str,
        worker_id: str,
        lease_token: str,
        fencing_token: int,
        lease_expires_at: str,
        heartbeat_at: str,
    ) -> ActionStepAttemptRow | None:
        return attempt_rows.heartbeat_attempt(
            transaction,
            tenant_id=tenant_id,
            attempt_id=attempt_id,
            worker_id=worker_id,
            lease_token=lease_token,
            fencing_token=fencing_token,
            lease_expires_at=lease_expires_at,
            heartbeat_at=heartbeat_at,
        )

    def lock_attempt_owner(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        attempt_id: str,
        worker_id: str,
        lease_token: str,
        fencing_token: int,
        owned_at: str,
    ) -> ActionStepAttemptRow | None:
        return attempt_rows.lock_attempt_owner(
            transaction,
            tenant_id=tenant_id,
            attempt_id=attempt_id,
            worker_id=worker_id,
            lease_token=lease_token,
            fencing_token=fencing_token,
            owned_at=owned_at,
        )

    def complete_attempt(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        attempt_id: str,
        worker_id: str,
        lease_token: str,
        fencing_token: int,
        status: str,
        output_manifest: dict[str, object],
        error: dict[str, object] | None,
        error_kind: str | None,
        completed_at: str,
        retry_at: str | None = None,
        external_execution_id: str | None = None,
    ) -> ActionStepAttemptRow | None:
        return attempt_rows.complete_attempt(
            transaction,
            tenant_id=tenant_id,
            attempt_id=attempt_id,
            worker_id=worker_id,
            lease_token=lease_token,
            fencing_token=fencing_token,
            status=status,
            output_manifest=output_manifest,
            error=error,
            error_kind=error_kind,
            completed_at=completed_at,
            retry_at=retry_at,
            external_execution_id=external_execution_id,
        )

    def append_event(self, *, transaction: Any, record: ActionRunEventRecord) -> ActionRunEventRow:
        return event_rows.append_event(transaction, record)

    def run_events(
        self, *, transaction: Any, tenant_id: str, run_id: str, after_sequence: int, limit: int
    ) -> list[ActionRunEventRow]:
        return event_rows.run_events(transaction, tenant_id, run_id, after_sequence, limit)
