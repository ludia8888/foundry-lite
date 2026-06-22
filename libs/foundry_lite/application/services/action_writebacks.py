from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.ports import (
    ACTION_RUN_COMPENSATION_REQUIRED,
    ACTION_RUN_FAILED,
    ACTION_RUN_OUTCOME_UNKNOWN,
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
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import (
    ConflictDetected,
    ExternalCompensationRequired,
    ExternalOutcomeUnknown,
    ExternalSystemError,
)


@dataclass(frozen=True)
class ActionWritebackRecorder:
    action_repository: ActionRepository
    runtime_service: ActionRuntimeBoundary

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
    ) -> str:
        now = _now()
        writeback_id = _new_id("writeback")
        request = _writeback_request(
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        self.action_repository.insert_action_writeback(
            transaction=conn,
            record=ActionWritebackRecord(
                writeback_id=writeback_id,
                tenant_id=ctx.tenant_id,
                action_run_id=action_run_id,
                mode="before_commit",
                connector_id=MOCK_WRITEBACK_CONNECTOR,
                request=request,
                response={**dict(response), "simulated": True},
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
            metadata={"status": status, "connector": MOCK_WRITEBACK_CONNECTOR, "mode": "before_commit"},
        )

    def fail_before_commit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> ExternalSystemError:
        error = _writeback_failure_error(idempotency_key=idempotency_key, request_hash=request_hash)
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

    def _mark_failed(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        error: ExternalSystemError,
    ) -> None:
        error_payload = dict(writeback_error_payload(self.runtime_service, error, ctx, action_run_id))
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

    def outcome_unknown_before_commit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> ExternalOutcomeUnknown:
        now = _now()
        details = _outcome_unknown_details(
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            reconciliation_deadline=now,
        )
        error = ExternalOutcomeUnknown("mock writeback outcome is unknown", details=dict(details))
        self._record_outcome_unknown(conn, ctx, action_run_id, idempotency_key, request_hash, details)
        self._mark_outcome_unknown(conn, ctx, action_run_id, error)
        return error

    def compensation_required_before_commit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> ExternalCompensationRequired:
        now = _now()
        details = _compensation_required_details(
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            reconciliation_deadline=now,
        )
        error = ExternalCompensationRequired("mock writeback requires compensation", details=dict(details))
        self._record_compensation_required(conn, ctx, action_run_id, idempotency_key, request_hash, details)
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
    ) -> None:
        self.record(
            conn,
            ctx,
            action_run_id,
            status="outcome_unknown",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
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
    ) -> None:
        self.record(
            conn,
            ctx,
            action_run_id,
            status="compensation_required",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
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
        error_payload = dict(writeback_error_payload(self.runtime_service, error, ctx, action_run_id))
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
        error_payload = dict(writeback_error_payload(self.runtime_service, error, ctx, action_run_id))
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


def _writeback_request(
    *,
    idempotency_key: str,
    request_hash: str,
) -> ActionWritebackPayload:
    return {
        "connector": MOCK_WRITEBACK_CONNECTOR,
        "simulated": True,
        "networkCall": False,
        "idempotency_key": idempotency_key,
        "request_hash": request_hash,
    }


def _writeback_failure_error(*, idempotency_key: str, request_hash: str) -> ExternalSystemError:
    return ExternalSystemError(
        "mock before-commit writeback failed",
        details={
            "connector": MOCK_WRITEBACK_CONNECTOR,
            "simulated": True,
            "idempotency_key": idempotency_key,
            "request_hash": request_hash,
        },
    )


def _outcome_unknown_details(
    *,
    idempotency_key: str,
    request_hash: str,
    reconciliation_deadline: str,
) -> ActionWritebackPayload:
    external_operation_id = f"mock-op-{idempotency_key}"
    return {
        "state": "OUTCOME_UNKNOWN",
        "connector": MOCK_WRITEBACK_CONNECTOR,
        "simulated": True,
        "external_operation_id": external_operation_id,
        "idempotency_key": idempotency_key,
        "request_hash": request_hash,
        "remote_resource_id": None,
        "last_observed_status": "unknown",
        "reconciliation_deadline": reconciliation_deadline,
    }


def _compensation_required_details(
    *,
    idempotency_key: str,
    request_hash: str,
    reconciliation_deadline: str,
) -> ActionWritebackPayload:
    external_operation_id = f"mock-op-{idempotency_key}"
    return {
        "state": "COMPENSATION_REQUIRED",
        "connector": MOCK_WRITEBACK_CONNECTOR,
        "simulated": True,
        "external_operation_id": external_operation_id,
        "idempotency_key": idempotency_key,
        "request_hash": request_hash,
        "remote_resource_id": f"mock-resource-{idempotency_key}",
        "last_observed_status": "succeeded",
        "compensation_action_type": "mock_reverse_writeback",
        "reconciliation_deadline": reconciliation_deadline,
    }
