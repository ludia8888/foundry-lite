"""Durable Builder MCP execution-run claim and completion ledger."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from foundry_lite.application.ports import (
    AiRunRepository,
    AiSessionRecord,
    AiToolCallRecord,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.ports.transaction_context import AI_RUN_SUCCEEDED
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.aip.agent_runtime_ledger import event_record
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import FdeMcpRequestBinding
from foundry_lite.application.services.aip.fde_mcp_contract import run_record
from foundry_lite.application.services.aip.fde_mcp_security import FdeMcpSecurityLedger
from foundry_lite.application.services.aip.fde_mcp_types import FdeMcpToolCall
from foundry_lite.application.services.aip.tool_broker import ToolSpec
from foundry_lite.domain.context import RequestContext
from foundry_lite.security.tenant_context import tenant_context


class FdeMcpRunLedger:
    repository: AiRunRepository

    def __init__(
        self,
        engine: TransactionManager,
        repository: AiRunRepository,
        security: FdeMcpSecurityLedger,
    ) -> None:
        self.engine = engine
        self.repository = repository
        self.security = security

    def seed(
        self,
        ctx: RequestContext,
        request: FdeMcpToolCall,
        binding: FdeMcpRequestBinding,
        run_id: str,
        catalog: tuple[ToolSpec, ...],
    ) -> bool:
        now = _now()
        with self._transaction(ctx) as conn:
            self.repository.create_session(
                transaction=conn,
                record=AiSessionRecord(
                    id=request.session_id,
                    tenant_id=ctx.tenant_id,
                    agent_version_id=f"builder-mcp:{request.application_id}:v1",
                    actor_user_id=ctx.actor_user_id,
                    status="active",
                    created_at=now,
                    last_activity_at=now,
                ),
            )
            existing = self.repository.insert_execution_run_or_get_existing(
                transaction=conn,
                record=run_record(ctx, request, binding, run_id, catalog, now),
            )
            if existing is not None:
                return False
            if request.confirmation_receipt is not None:
                self.security.consume_in_transaction(conn, ctx, request.confirmation_receipt, binding)
            self.repository.append_execution_event(
                transaction=conn,
                record=event_record(ctx, run_id, 1, "mcp_tool_running", {"toolId": request.tool_id}, now),
            )
            return True

    def complete(self, ctx: RequestContext, run_id: str, tool_record: AiToolCallRecord) -> None:
        now = _now()
        with self._transaction(ctx) as conn:
            self.repository.record_tool_call(transaction=conn, record=tool_record)
            self.repository.append_execution_event(
                transaction=conn,
                record=event_record(ctx, run_id, 2, "succeeded", {"source": "builder_mcp"}, now),
            )
            self.repository.update_execution_run_status(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                ai_run_id=run_id,
                transition=AI_RUN_SUCCEEDED,
                usage_json={"modelCallCount": 0, "toolCallCount": 1, "source": "builder_mcp"},
                error_json=None,
                completed_at=now,
            )

    @contextmanager
    def _transaction(self, ctx: RequestContext) -> Iterator[TransactionContext]:
        """Begin every Builder run-ledger transaction for the authenticated tenant."""

        with tenant_context(ctx.tenant_id):
            with self.engine.begin() as conn:
                yield conn
