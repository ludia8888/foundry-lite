"""Application service helpers for action writebacks workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.ports import (
    ACTION_RUN_COMPENSATION_REQUIRED,
    ACTION_RUN_FAILED,
    ACTION_RUN_OUTCOME_UNKNOWN,
    ACTION_RUN_RETRYABLE,
    TransactionContext,
)
from foundry_lite.application.ports.action_repository import (
    ActionRepository,
    ActionWritebackPayload,
    ActionWritebackRecord,
)
from foundry_lite.application.primitives import MOCK_WRITEBACK_CONNECTOR, _new_id, _now
from foundry_lite.application.services.action_helpers import writeback_error_payload
from foundry_lite.application.services.action_protocols import ActionRuntimeBoundary
from foundry_lite.application.services.action_writeback_payloads import (
    compensation_required_details,
    outcome_unknown_details,
    retryable_details,
    writeback_failure_error,
    writeback_request,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    ExternalCompensationRequired,
    ExternalOutcomeUnknown,
    ExternalRetryableWriteback,
    ExternalSystemError,
)


class SimulatedWritebackCommand(Protocol):
    @property
    def idempotency_key(self) -> str: ...

    @property
    def request_fingerprint(self) -> str: ...

    @property
    def simulate_writeback_failure(self) -> bool: ...

    @property
    def simulate_writeback_retryable(self) -> bool: ...

    @property
    def simulate_writeback_outcome_unknown(self) -> bool: ...

    @property
    def simulate_writeback_compensation_required(self) -> bool: ...


@dataclass(frozen=True)
class ActionWritebackRecorder:
    action_repository: ActionRepository
    runtime_service: ActionRuntimeBoundary
    connector_id: str = MOCK_WRITEBACK_CONNECTOR
    is_simulated: bool = True

    def record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        *,
        status: str,
        idempotency_key: str,
        request_hash: str,
        response: ActionWritebackPayload,
        external_writeback_uri: str | None = None,
    ) -> str:
        now = _now()
        writeback_id = _new_id("writeback")
        request = writeback_request(
            connector_id=self.connector_id,
            is_simulated=self.is_simulated,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            external_writeback_uri=external_writeback_uri,
        )
        self.action_repository.insert_action_writeback(
            transaction=conn,
            record=ActionWritebackRecord(
                writeback_id=writeback_id,
                tenant_id=ctx.tenant_id,
                action_run_id=action_run_id,
                mode="before_commit",
                connector_id=self.connector_id,
                request=request,
                response={**dict(response), "simulated": self.is_simulated},
                status=status,
                idempotency_key=idempotency_key,
                attempts=1,
                created_at=now,
                completed_at=now,
            ),
        )
        self._record_writeback_relation(conn, ctx, action_run_id, writeback_id, status)
        return writeback_id

    def _record_writeback_relation(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        writeback_id: str,
        status: str,
    ) -> None:
        """Record the action->writeback run relation."""
        self.runtime_service._run_relation(
            conn,
            ctx,
            source_run_type="action",
            source_run_id=action_run_id,
            target_run_type="action_writeback",
            target_run_id=writeback_id,
            relation="writeback_attempt",
            resource_type="action_writeback",
            resource_id=writeback_id,
            metadata={"status": status, "connector": self.connector_id, "mode": "before_commit"},
        )

    def simulated_before_commit_error(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        command: SimulatedWritebackCommand,
    ) -> ExternalSystemError | None:
        """Dispatch the requested simulated before-commit writeback outcome (or None for no simulation)."""
        if command.simulate_writeback_failure:
            return self.fail_before_commit(
                conn, ctx, action_run_id, command.idempotency_key, command.request_fingerprint
            )
        if command.simulate_writeback_retryable:
            return self.retryable_before_commit(
                conn, ctx, action_run_id, command.idempotency_key, command.request_fingerprint
            )
        if command.simulate_writeback_outcome_unknown:
            return self.outcome_unknown_before_commit(
                conn, ctx, action_run_id, command.idempotency_key, command.request_fingerprint
            )
        if command.simulate_writeback_compensation_required:
            return self.compensation_required_before_commit(
                conn, ctx, action_run_id, command.idempotency_key, command.request_fingerprint
            )
        return None

    def fail_before_commit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> ExternalSystemError:
        error = writeback_failure_error(idempotency_key=idempotency_key, request_hash=request_hash)
        self.record(
            conn,
            ctx,
            action_run_id,
            status="failed",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response={"status_code": 500},
        )
        self._mark_failed(conn, ctx, action_run_id, error)
        return error

    def retryable_before_commit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> ExternalRetryableWriteback:
        details = retryable_details(
            connector_id=self.connector_id,
            is_simulated=self.is_simulated,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        error = ExternalRetryableWriteback(
            "writeback failed before changing the external system",
            details=dict(details),
        )
        self.record(
            conn,
            ctx,
            action_run_id,
            status="retryable",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            response=details,
        )
        self._mark_retryable(conn, ctx, action_run_id, error)
        return error

    def _mark_failed(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        error: ExternalSystemError,
    ) -> None:
        error_payload = dict(
            writeback_error_payload(self.runtime_service, error, ctx, action_run_id, connector_id=self.connector_id)
        )
        updated = self.action_repository.update_action_run_terminal(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            action_run_id=action_run_id,
            transition=ACTION_RUN_FAILED,
            error=error_payload,
            completed_at=_now(),
        )
        if not updated:
            raise ConflictDetected("action run terminal state changed concurrently")
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="action.run.failed",
            resource_type="action_run",
            resource_id=action_run_id,
            action="apply",
            after_ref=error_payload,
            correlation_id=action_run_id,
        )

    def _mark_retryable(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        error: ExternalRetryableWriteback,
    ) -> None:
        error_payload = dict(
            writeback_error_payload(self.runtime_service, error, ctx, action_run_id, connector_id=self.connector_id)
        )
        updated = self.action_repository.update_action_run_terminal(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            action_run_id=action_run_id,
            transition=ACTION_RUN_RETRYABLE,
            error=error_payload,
            completed_at=_now(),
        )
        if not updated:
            raise ConflictDetected("action run terminal state changed concurrently")
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="action.run.retryable",
            resource_type="action_run",
            resource_id=action_run_id,
            action="apply",
            after_ref=error_payload,
            correlation_id=action_run_id,
        )

    def outcome_unknown_before_commit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        idempotency_key: str,
        request_hash: str,
        *,
        remote_resource_id: str | None = None,
        external_writeback_uri: str | None = None,
    ) -> ExternalOutcomeUnknown:
        now = _now()
        details = outcome_unknown_details(
            connector_id=self.connector_id,
            is_simulated=self.is_simulated,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            reconciliation_deadline=now,
            remote_resource_id=remote_resource_id,
        )
        error = ExternalOutcomeUnknown("writeback outcome is unknown", details=dict(details))
        self._record_outcome_unknown(
            conn, ctx, action_run_id, idempotency_key, request_hash, details, external_writeback_uri
        )
        self._mark_outcome_unknown(conn, ctx, action_run_id, error)
        return error

    def compensation_required_before_commit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        idempotency_key: str,
        request_hash: str,
        *,
        remote_resource_id: str | None = None,
        external_writeback_uri: str | None = None,
    ) -> ExternalCompensationRequired:
        now = _now()
        details = compensation_required_details(
            connector_id=self.connector_id,
            is_simulated=self.is_simulated,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            reconciliation_deadline=now,
            remote_resource_id=remote_resource_id,
        )
        error = ExternalCompensationRequired("writeback requires compensation", details=dict(details))
        self._record_compensation_required(
            conn, ctx, action_run_id, idempotency_key, request_hash, details, external_writeback_uri
        )
        self._mark_compensation_required(conn, ctx, action_run_id, error)
        return error

    def _record_outcome_unknown(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        idempotency_key: str,
        request_hash: str,
        details: ActionWritebackPayload,
        external_writeback_uri: str | None,
    ) -> None:
        self.record(
            conn,
            ctx,
            action_run_id,
            status="outcome_unknown",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            external_writeback_uri=external_writeback_uri,
            response={
                "status_code": None,
                "outcome_unknown": True,
                "last_observed_status": details["last_observed_status"],
                "external_operation_id": details["external_operation_id"],
                "remote_resource_id": details["remote_resource_id"],
                "reconciliation_deadline": details["reconciliation_deadline"],
            },
        )

    def _record_compensation_required(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        idempotency_key: str,
        request_hash: str,
        details: ActionWritebackPayload,
        external_writeback_uri: str | None,
    ) -> None:
        self.record(
            conn,
            ctx,
            action_run_id,
            status="compensation_required",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            external_writeback_uri=external_writeback_uri,
            response={
                "status_code": 200,
                "compensation_required": True,
                "last_observed_status": details["last_observed_status"],
                "external_operation_id": details["external_operation_id"],
                "remote_resource_id": details["remote_resource_id"],
                "compensation_action_type": details["compensation_action_type"],
                "reconciliation_deadline": details["reconciliation_deadline"],
            },
        )

    def _mark_outcome_unknown(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        error: ExternalOutcomeUnknown,
    ) -> None:
        error_payload = dict(
            writeback_error_payload(self.runtime_service, error, ctx, action_run_id, connector_id=self.connector_id)
        )
        updated = self.action_repository.update_action_run_terminal(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            action_run_id=action_run_id,
            transition=ACTION_RUN_OUTCOME_UNKNOWN,
            error=error_payload,
            completed_at=_now(),
        )
        if not updated:
            raise ConflictDetected("action run terminal state changed concurrently")
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="action.run.outcome_unknown",
            resource_type="action_run",
            resource_id=action_run_id,
            action="apply",
            after_ref=error_payload,
            correlation_id=action_run_id,
        )

    def _mark_compensation_required(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        error: ExternalCompensationRequired,
    ) -> None:
        error_payload = dict(
            writeback_error_payload(self.runtime_service, error, ctx, action_run_id, connector_id=self.connector_id)
        )
        updated = self.action_repository.update_action_run_terminal(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            action_run_id=action_run_id,
            transition=ACTION_RUN_COMPENSATION_REQUIRED,
            error=error_payload,
            completed_at=_now(),
        )
        if not updated:
            raise ConflictDetected("action run terminal state changed concurrently")
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="action.run.compensation_required",
            resource_type="action_run",
            resource_id=action_run_id,
            action="apply",
            after_ref=error_payload,
            correlation_id=action_run_id,
        )
