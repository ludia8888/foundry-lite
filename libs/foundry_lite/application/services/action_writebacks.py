from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.action_repository import (
    ActionRepository,
    ActionWritebackPayload,
    ActionWritebackRecord,
)
from foundry_lite.application.primitives import MOCK_WRITEBACK_CONNECTOR, _new_id, _now
from foundry_lite.application.services.action_helpers import writeback_error_payload
from foundry_lite.application.services.action_protocols import ActionRuntimeBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ExternalSystemError


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
        response: ActionWritebackPayload,
    ) -> None:
        now = _now()
        self.action_repository.insert_action_writeback(
            transaction=conn,
            record=ActionWritebackRecord(
                writeback_id=_new_id("writeback"),
                tenant_id=ctx.tenant_id,
                action_run_id=action_run_id,
                mode="before_commit",
                connector_id=MOCK_WRITEBACK_CONNECTOR,
                request={"connector": MOCK_WRITEBACK_CONNECTOR, "simulated": True, "networkCall": False},
                response={**dict(response), "simulated": True},
                status=status,
                idempotency_key=idempotency_key,
                attempts=1,
                created_at=now,
                completed_at=now,
            ),
        )

    def fail_before_commit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_run_id: str,
        idempotency_key: str,
    ) -> ExternalSystemError:
        error = ExternalSystemError(
            "mock before-commit writeback failed",
            details={"connector": MOCK_WRITEBACK_CONNECTOR, "simulated": True},
        )
        self.record(
            conn,
            ctx,
            action_run_id,
            status="failed",
            idempotency_key=idempotency_key,
            response={"status_code": 500},
        )
        error_payload = dict(writeback_error_payload(self.runtime_service, error, ctx, action_run_id))
        self.action_repository.update_action_run_terminal(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            action_run_id=action_run_id,
            status="failed",
            error=error_payload,
            completed_at=_now(),
        )
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
        return error
