"""Replay identity and human-approved one-time receipts for Builder MCP."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from foundry_lite.application.ports import AiRunRepository, AiSessionRecord, TransactionContext, TransactionManager
from foundry_lite.application.ports.transaction_context import AI_RUN_FAILED, AI_RUN_SUCCEEDED
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.aip.agent_runtime_ledger import event_record, hash_json
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import (
    FdeMcpReplay,
    FdeMcpRequestBinding,
)
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import (
    challenge_binding as _challenge_binding,
)
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import (
    challenge_id as _challenge_id,
)
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import (
    challenge_payload as _challenge_payload,
)
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import (
    challenge_record as _challenge_record,
)
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import (
    challenge_replay_conflict_reason as _challenge_replay_conflict_reason,
)
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import (
    confirmation_conflict as _confirmation_conflict,
)
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import (
    execution_binding_matches as _execution_binding_matches,
)
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import (
    existing_challenge_payload as _existing_challenge_payload,
)
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import (
    expires_at as _expires_at,
)
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import (
    failed_replay as _failed_replay,
)
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import (
    next_sequence as _next_sequence,
)
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import (
    receipt_conflict_reason as _receipt_conflict_reason,
)
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import (
    receipt_record as _receipt_record,
)
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import (
    replay_conflict as _replay_conflict,
)
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import (
    require_human_control_principal as _require_human_control_principal,
)
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import (
    terminal_replay as _terminal_replay,
)
from foundry_lite.application.services.aip.fde_mcp_widget_confirmation import (
    FdeMcpWidgetConfirmationLedger,
)
from foundry_lite.application.services.mcp_tool_results import tool_error_structured
from foundry_lite.application.services.runtime_error_payloads import scrub_error_text
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError
from foundry_lite.security.policy import PolicyService
from foundry_lite.security.tenant_context import tenant_context


class FdeMcpSecurityLedger:
    """Own durable replay conflicts and confirmation challenge transitions."""

    repository: AiRunRepository
    widget_confirmation: FdeMcpWidgetConfirmationLedger

    def __init__(self, engine: TransactionManager, repository: AiRunRepository, policy: PolicyService) -> None:
        self.engine = engine
        self.repository = repository
        self.policy = policy
        self.widget_confirmation = FdeMcpWidgetConfirmationLedger(engine, repository, policy)

    def replay(self, ctx: RequestContext, run_id: str, binding: FdeMcpRequestBinding) -> FdeMcpReplay | None:
        conflict_reason: str | None = None
        with self._transaction(ctx) as conn:
            ledger = self.repository.ledger_for_run(transaction=conn, tenant_id=ctx.tenant_id, ai_run_id=run_id)
            if ledger is None:
                return None
            if not _execution_binding_matches(ledger["run"], binding):
                conflict_reason = "request_binding_mismatch"
                self._append_conflict(conn, ctx, run_id, ledger["events"], conflict_reason, binding)
            elif ledger["run"].get("status") == "failed":
                replay = _failed_replay(ledger["run"])
                if replay is not None:
                    return replay
                conflict_reason = "failed_tool_evidence_missing"
                self._append_conflict(conn, ctx, run_id, ledger["events"], conflict_reason, binding)
            elif ledger["run"].get("status") != "succeeded":
                conflict_reason = "request_already_in_progress_or_failed"
                self._append_conflict(conn, ctx, run_id, ledger["events"], conflict_reason, binding)
            else:
                replay = _terminal_replay(ledger["toolCalls"])
                if replay is not None:
                    return replay
                conflict_reason = "terminal_tool_evidence_missing"
                self._append_conflict(conn, ctx, run_id, ledger["events"], conflict_reason, binding)
        raise _replay_conflict(run_id, conflict_reason or "unknown")

    def fail_execution(self, ctx: RequestContext, run_id: str, exc: Exception) -> None:
        now = _now()
        error = _execution_error(ctx, exc)
        with self._transaction(ctx) as conn:
            self.repository.append_execution_event(
                transaction=conn,
                record=event_record(ctx, run_id, 2, "failed", error, now),
            )
            self.repository.update_execution_run_status(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                ai_run_id=run_id,
                transition=AI_RUN_FAILED,
                usage_json={"modelCallCount": 0, "toolCallCount": 0, "source": "builder_mcp"},
                error_json=error,
                completed_at=now,
            )

    def issue_challenge(
        self,
        ctx: RequestContext,
        run_id: str,
        binding: FdeMcpRequestBinding,
    ) -> dict[str, object]:
        expires_at = _expires_at(now := _now())
        challenge_id = _challenge_id(run_id)
        replay_result: tuple[dict[str, object] | None, str | None] | None = None
        with self._transaction(ctx) as conn:
            self._create_session(conn, ctx, binding, now)
            existing = self.repository.insert_execution_run_or_get_existing(
                transaction=conn,
                record=_challenge_record(ctx, binding, challenge_id, now, expires_at),
            )
            if existing is None:
                widget_token = self.widget_confirmation.issue_in_transaction(
                    conn, ctx, binding, challenge_id, now, expires_at
                )
                self.repository.append_execution_event(
                    transaction=conn,
                    record=event_record(
                        ctx,
                        challenge_id,
                        1,
                        "mcp_confirmation_approval_required",
                        {"toolId": binding.tool_id, "expiresAt": expires_at},
                        now,
                    ),
                )
                challenge_result = _challenge_payload(challenge_id, binding, expires_at)
                if widget_token is not None:
                    challenge_result["_meta"] = {"widgetApprovalToken": widget_token}
                return challenge_result
            ledger = self.repository.ledger_for_run(transaction=conn, tenant_id=ctx.tenant_id, ai_run_id=challenge_id)
            replay_result = self._replayed_challenge(conn, ctx, challenge_id, binding, ledger, now)
        payload, reason = replay_result or (None, "challenge_insert_conflict")
        if reason is not None:
            raise _confirmation_conflict(reason)
        return payload or _challenge_payload(challenge_id, binding, expires_at)

    def _replayed_challenge(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        challenge_id: str,
        binding: FdeMcpRequestBinding,
        ledger: Mapping[str, object] | None,
        now: str,
    ) -> tuple[dict[str, object] | None, str | None]:
        if ledger is None:
            return None, "challenge_insert_conflict"
        reason = _challenge_replay_conflict_reason(ledger, binding, now)
        if reason is not None:
            event_value = ledger.get("events")
            events = [row for row in event_value if isinstance(row, Mapping)] if isinstance(event_value, list) else []
            self._append_conflict(conn, ctx, challenge_id, events, reason, binding)
            return None, reason
        payload = _existing_challenge_payload(ledger, challenge_id, binding)
        run = ledger.get("run")
        if isinstance(run, Mapping) and run.get("status") == "succeeded":
            recovered = self.widget_confirmation.recover_receipt_in_transaction(
                conn, ctx, ledger, binding, challenge_id, now
            )
            if recovered is not None:
                payload["_meta"] = {"confirmationReceipt": recovered[0]}
            return payload, None
        widget_token = self.widget_confirmation.rotate_in_transaction(
            conn, ctx, binding, challenge_id, ledger, now, _expires_at(now)
        )
        if widget_token is not None:
            payload["_meta"] = {"widgetApprovalToken": widget_token}
        return payload, None

    def approve(self, ctx: RequestContext, application_id: str, challenge_id: str) -> dict[str, object]:
        _require_human_control_principal(ctx)
        self.policy.require(ctx, "aip:mcp:confirm")
        receipt_id = _new_id("aip_mcp_receipt")
        now = _now()
        expires_at = _expires_at(now)
        with self._transaction(ctx) as conn:
            ledger = self.repository.ledger_for_run(transaction=conn, tenant_id=ctx.tenant_id, ai_run_id=challenge_id)
            binding = _challenge_binding(ledger, application_id, now)
            self.policy.require(ctx, binding.required_permission)
            if ledger is not None and ledger["run"].get("status") == "succeeded":
                return self._approved_receipt_response(conn, ctx, ledger, binding, now)
            self._revoke_widget_token_for_control(conn, ctx, ledger, now)
            updated = self.repository.update_execution_run_status(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                ai_run_id=challenge_id,
                transition=AI_RUN_SUCCEEDED,
                usage_json={
                    "source": "builder_mcp_human_approval",
                    "confirmationReceipt": receipt_id,
                    "receiptExpiresAt": expires_at,
                    "approvedByUserId": ctx.actor_user_id,
                },
                error_json=None,
                completed_at=now,
            )
            if updated is None:
                refreshed = self.repository.ledger_for_run(
                    transaction=conn, tenant_id=ctx.tenant_id, ai_run_id=challenge_id
                )
                return self._approved_receipt_response(conn, ctx, refreshed, binding, now)
            self._record_approval(conn, ctx, challenge_id, receipt_id, binding, now, expires_at)
        return {
            "status": "approved",
            "challengeId": challenge_id,
            "confirmationReceipt": receipt_id,
            "expiresAt": expires_at,
        }

    def _revoke_widget_token_for_control(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ledger: Mapping[str, object] | None,
        now: str,
    ) -> None:
        if ledger is not None:
            self.widget_confirmation.revoke_active_in_transaction(conn, ctx, ledger, now, "human_control_approval")

    def approve_widget(
        self,
        ctx: RequestContext,
        application_id: str,
        session_id: str,
        challenge_id: str,
        widget_approval_token: str,
        origin: str | None,
    ) -> dict[str, object]:
        return self.widget_confirmation.approve(
            ctx,
            application_id,
            session_id,
            challenge_id,
            widget_approval_token,
            origin,
        )

    def is_widget_approval_recovery(
        self,
        ctx: RequestContext,
        application_id: str,
        session_id: str,
        challenge_id: str,
        widget_approval_token: str,
        origin: str | None,
    ) -> bool:
        return self.widget_confirmation.is_recovery(
            ctx,
            application_id,
            session_id,
            challenge_id,
            widget_approval_token,
            origin,
        )

    def _approved_receipt_response(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        challenge: Mapping[str, object] | None,
        binding: FdeMcpRequestBinding,
        now: str,
    ) -> dict[str, object]:
        run = challenge.get("run") if isinstance(challenge, Mapping) else None
        usage = run.get("usage_json") if isinstance(run, Mapping) else None
        receipt_id = usage.get("confirmationReceipt") if isinstance(usage, Mapping) else None
        if not isinstance(receipt_id, str):
            raise _confirmation_conflict("approved_receipt_missing")
        receipt = self.repository.ledger_for_run(transaction=conn, tenant_id=ctx.tenant_id, ai_run_id=receipt_id)
        reason = _receipt_conflict_reason(receipt, binding, now)
        if reason is not None:
            raise _confirmation_conflict(reason)
        budget = receipt["run"].get("budget_json") if receipt is not None else None
        expires_at = budget.get("expiresAt") if isinstance(budget, Mapping) else None
        return {
            "status": "approved",
            "challengeId": str(run.get("id")) if isinstance(run, Mapping) else "",
            "confirmationReceipt": receipt_id,
            "expiresAt": expires_at,
        }

    def consume_in_transaction(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        receipt_id: str,
        binding: FdeMcpRequestBinding,
    ) -> None:
        conflict_reason: str | None = None
        ledger = self.repository.ledger_for_run(transaction=conn, tenant_id=ctx.tenant_id, ai_run_id=receipt_id)
        conflict_reason = _receipt_conflict_reason(ledger, binding, _now())
        if conflict_reason is not None:
            if ledger is not None:
                self._append_conflict(conn, ctx, receipt_id, ledger["events"], conflict_reason, binding)
        else:
            updated = self.repository.update_execution_run_status(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                ai_run_id=receipt_id,
                transition=AI_RUN_SUCCEEDED,
                usage_json={"source": "builder_mcp_confirmation_consumed"},
                error_json=None,
                completed_at=_now(),
            )
            if updated is not None:
                self._append_event(conn, ctx, receipt_id, ledger, "mcp_confirmation_consumed", binding)
                return
            conflict_reason = "receipt_already_consumed"
        raise _confirmation_conflict(conflict_reason or "receipt_invalid")

    def _record_approval(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        challenge_id: str,
        receipt_id: str,
        binding: FdeMcpRequestBinding,
        now: str,
        expires_at: str,
    ) -> None:
        self.repository.append_execution_event(
            transaction=conn,
            record=event_record(
                ctx,
                challenge_id,
                2,
                "mcp_confirmation_approved",
                {"approvedByUserId": ctx.actor_user_id, "receiptHash": hash_json(receipt_id)},
                now,
            ),
        )
        self.repository.create_execution_run(
            transaction=conn,
            record=_receipt_record(ctx, binding, receipt_id, challenge_id, now, expires_at),
        )
        self.repository.append_execution_event(
            transaction=conn,
            record=event_record(
                ctx,
                receipt_id,
                1,
                "mcp_confirmation_receipt_issued",
                {"challengeId": challenge_id, "approvedByUserId": ctx.actor_user_id, "expiresAt": expires_at},
                now,
            ),
        )

    def _create_session(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        binding: FdeMcpRequestBinding,
        now: str,
    ) -> None:
        self.repository.create_session(
            transaction=conn,
            record=AiSessionRecord(
                id=binding.session_id,
                tenant_id=ctx.tenant_id,
                agent_version_id=f"builder-mcp:{binding.application_id}:v1",
                actor_user_id=binding.actor_user_id,
                status="active",
                created_at=now,
                last_activity_at=now,
            ),
        )

    @contextmanager
    def _transaction(self, ctx: RequestContext) -> Iterator[TransactionContext]:
        """Begin every Builder security-ledger transaction for the authenticated tenant."""

        with tenant_context(ctx.tenant_id):
            with self.engine.begin() as conn:
                yield conn

    def _append_conflict(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        events: list[Mapping[str, object]],
        reason: str,
        binding: FdeMcpRequestBinding,
    ) -> None:
        self.repository.append_execution_event(
            transaction=conn,
            record=event_record(
                ctx,
                run_id,
                _next_sequence(events),
                "mcp_security_conflict",
                {"reason": reason, "requestBindingHash": binding.fingerprint},
                _now(),
            ),
        )

    def _append_event(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        ledger: Mapping[str, object] | None,
        event_type: str,
        binding: FdeMcpRequestBinding,
    ) -> None:
        events = ledger.get("events") if isinstance(ledger, Mapping) else None
        rows = events if isinstance(events, list) else []
        self.repository.append_execution_event(
            transaction=conn,
            record=event_record(
                ctx,
                run_id,
                _next_sequence(rows),
                event_type,
                {"requestBindingHash": binding.fingerprint},
                _now(),
            ),
        )


def _execution_error(ctx: RequestContext, exc: Exception) -> dict[str, object]:
    error: dict[str, object] = {"type": type(exc).__name__, "detail": scrub_error_text(str(exc))[:512]}
    if isinstance(exc, FoundryLiteError):
        error["mcpToolResult"] = tool_error_structured(exc, request_id=ctx.request_id)
    return error
