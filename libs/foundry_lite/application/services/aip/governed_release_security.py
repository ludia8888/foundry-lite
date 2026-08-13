"""Durable one-time widget receipts and idempotent release MCP run evidence."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import (
    AiRunRepository,
    AiSessionRecord,
    AiToolCallRecord,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.ports.transaction_context import (
    AI_RUN_FAILED,
    AI_RUN_RETRY_RUNNING,
    AI_RUN_SUCCEEDED,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.aip import governed_release_run_evidence as run_evidence
from foundry_lite.application.services.aip.agent_runtime_ledger import event_record
from foundry_lite.application.services.aip.governed_release_audit import (
    GovernedReleaseAuditBoundary,
    _audit,
    audit_release_action,
)
from foundry_lite.application.services.aip.governed_release_preparation import (
    append_initial_preparation_events,
    rotate_preparation_receipt,
)
from foundry_lite.application.services.aip.governed_release_security_contract import (
    GovernedReleaseBinding,
    GovernedReleaseReplay,
    action_record,
    failed_retry_budget,
    preparation_record,
    preparation_run_id,
    receipt_conflict_reason,
    receipt_expires_at,
    receipt_record,
    recovery_attempt,
    recovery_budget,
    release_conflict,
    replay_from_ledger,
    widget_receipt_id,
)
from foundry_lite.application.services.aip.governed_release_terminal_evidence import (
    append_failure_evidence,
    append_outcome_unknown_evidence,
    append_success_evidence,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.security.policy import PolicyService

JsonObject = Mapping[str, object]


class GovernedReleaseSecurityLedger:
    """Own preparation, one-time consumption, and terminal replay transitions."""

    repository: AiRunRepository
    audit: GovernedReleaseAuditBoundary

    def __init__(
        self,
        engine: TransactionManager,
        repository: AiRunRepository,
        policy: PolicyService,
        audit: GovernedReleaseAuditBoundary,
    ) -> None:
        self.engine = engine
        self.repository = repository
        self.policy = policy
        self.audit = audit

    def authorize(self, ctx: RequestContext, binding: GovernedReleaseBinding) -> None:
        self.policy.require(ctx, "aip:mcp:confirm")
        self.policy.require(ctx, binding.required_permission)

    def prepare(self, ctx: RequestContext, binding: GovernedReleaseBinding) -> dict[str, object]:
        self.authorize(ctx, binding)
        now = _now()
        expires_at = receipt_expires_at(now)
        run_id = preparation_run_id(binding)
        receipt_secret = _new_id("governed_release_widget_secret")
        receipt_id = widget_receipt_id(receipt_secret)
        record = preparation_record(ctx, binding, run_id, receipt_id, now, expires_at)
        with self.engine.begin() as conn:
            self._create_session(conn, ctx, binding, now)
            existing = self.repository.insert_execution_run_or_get_existing(transaction=conn, record=record)
            if existing is not None:
                return rotate_preparation_receipt(self.repository, conn, ctx, binding, existing, run_id, now)
            self.repository.create_execution_run(
                transaction=conn,
                record=receipt_record(ctx, binding, receipt_id, run_id, now, expires_at),
            )
            append_initial_preparation_events(self.repository, conn, ctx, binding, run_id, receipt_id, now, expires_at)
        return run_evidence.prepared_payload(run_id, receipt_secret, expires_at, is_replayed=False)

    def replay(
        self,
        ctx: RequestContext,
        run_id: str,
        binding: GovernedReleaseBinding,
    ) -> GovernedReleaseReplay | None:
        self.authorize(ctx, binding)
        with self.engine.begin() as conn:
            ledger = self.repository.ledger_for_run(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                ai_run_id=run_id,
            )
        return replay_from_ledger(ledger, binding) if ledger is not None else None

    def claim(
        self,
        ctx: RequestContext,
        run_id: str,
        binding: GovernedReleaseBinding,
        widget_confirmation_token: str,
    ) -> bool:
        self.authorize(ctx, binding)
        now = _now()
        with self.engine.begin() as conn:
            self._create_session(conn, ctx, binding, now)
            existing = self.repository.insert_execution_run_or_get_existing(
                transaction=conn,
                record=action_record(ctx, binding, run_id, now),
            )
            if existing is not None:
                return False
            self._consume_receipt(conn, ctx, widget_confirmation_token, binding, now)
            self.repository.append_execution_event(
                transaction=conn,
                record=event_record(
                    ctx,
                    run_id,
                    1,
                    "governed_release_tool_running",
                    {"toolName": binding.tool_name, "source": "governed_release_mcp"},
                    now,
                ),
            )
            audit_release_action(conn, ctx, self.audit, binding, run_id, "started")
        return True

    def recover(
        self,
        ctx: RequestContext,
        run_id: str,
        binding: GovernedReleaseBinding,
    ) -> int | None:
        """Acquire one stale-run recovery lease without asking for a second receipt."""
        self.authorize(ctx, binding)
        now = _now()
        with self.engine.begin() as conn:
            return self._claim_recovery(conn, ctx, run_id, binding, now)

    def is_fresh_failed_retry(
        self,
        ctx: RequestContext,
        run_id: str,
        binding: GovernedReleaseBinding,
        widget_confirmation_token: str,
    ) -> bool:
        """Preflight a fresh receipt so terminal replay never consumes action quota."""
        self.authorize(ctx, binding)
        now = _now()
        with self.engine.begin() as conn:
            run = self.repository.execution_run_by_id(transaction=conn, tenant_id=ctx.tenant_id, ai_run_id=run_id)
            if run is None or run.get("status") != "failed":
                return False
            failed_retry_budget(run, binding, now)
            receipt = self.repository.ledger_for_run(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                ai_run_id=widget_receipt_id(widget_confirmation_token),
            )
            reason = receipt_conflict_reason(receipt, binding, now)
        if reason == "widget_confirmation_already_consumed":
            return False
        if reason is not None:
            raise release_conflict(reason)
        return True

    def retry_failed(
        self,
        ctx: RequestContext,
        run_id: str,
        binding: GovernedReleaseBinding,
        widget_confirmation_token: str,
    ) -> int | None:
        """Reopen only a proven no-commit failure with a fresh human receipt."""
        self.authorize(ctx, binding)
        now = _now()
        with self.engine.begin() as conn:
            run = self.repository.execution_run_by_id(transaction=conn, tenant_id=ctx.tenant_id, ai_run_id=run_id)
            if run is None or run.get("status") != "failed":
                return None
            replacement = failed_retry_budget(run, binding, now)
            self._consume_receipt(conn, ctx, widget_confirmation_token, binding, now)
            claimed = self.repository.compare_and_swap_execution_run_budget(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                ai_run_id=run_id,
                expected_status="failed",
                expected_budget_json=run_evidence.budget(run),
                replacement_budget_json=replacement,
            )
            if claimed is None:
                raise release_conflict("release_failed_retry_conflict")
            return self._start_failed_retry(conn, ctx, run_id, binding, claimed, now)

    def _start_failed_retry(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        binding: GovernedReleaseBinding,
        claimed: JsonObject,
        now: str,
    ) -> int:
        reopened = self.repository.update_execution_run_status(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ai_run_id=run_id,
            transition=AI_RUN_RETRY_RUNNING,
            usage_json={"source": "governed_release_mcp_failed_retry"},
            error_json=None,
            completed_at=None,
        )
        if reopened is None:
            raise release_conflict("release_failed_retry_conflict")
        attempt = recovery_attempt(claimed)
        self.repository.append_execution_event(
            transaction=conn,
            record=event_record(
                ctx,
                run_id,
                run_evidence.recovery_sequence(attempt),
                "governed_release_failed_run_reopened",
                {"toolName": binding.tool_name, "attempt": attempt, "knownNotCommitted": True},
                now,
            ),
        )
        _audit(conn, ctx, self.audit, run_id, attempt)
        audit_release_action(conn, ctx, self.audit, binding, run_id, "retry_started", attempt=attempt)
        return attempt

    def _claim_recovery(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        binding: GovernedReleaseBinding,
        now: str,
    ) -> int | None:
        ledger = self.repository.ledger_for_run(transaction=conn, tenant_id=ctx.tenant_id, ai_run_id=run_id)
        if ledger is None:
            return None
        current = ledger["run"]
        claimed = self.repository.claim_execution_run_recovery(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ai_run_id=run_id,
            expected_budget_json=run_evidence.budget(current),
            recovery_budget_json=recovery_budget(current, binding, now),
        )
        if claimed is None:
            return None
        attempt = recovery_attempt(claimed)
        self.repository.append_execution_event(
            transaction=conn,
            record=event_record(
                ctx,
                run_id,
                run_evidence.recovery_sequence(attempt),
                "governed_release_recovery_claimed",
                {"toolName": binding.tool_name, "attempt": attempt},
                now,
            ),
        )
        audit_release_action(conn, ctx, self.audit, binding, run_id, "recovery_started", attempt=attempt)
        return attempt

    def complete(
        self,
        ctx: RequestContext,
        run_id: str,
        binding: GovernedReleaseBinding,
        output: JsonObject,
        execution_attempt: int = 0,
    ) -> str:
        now = _now()
        tool_record = run_evidence.tool_record(ctx, run_id, binding, output, now)
        with self.engine.begin() as conn:
            self._complete_run(conn, ctx, run_id, binding, tool_record, execution_attempt, now)
        return tool_record.id

    def _complete_run(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        run_id: str,
        binding: GovernedReleaseBinding,
        tool_record: AiToolCallRecord,
        execution_attempt: int,
        now: str,
    ) -> None:
        run = self.repository.execution_run_by_id(transaction=conn, tenant_id=ctx.tenant_id, ai_run_id=run_id)
        if run is None:
            raise release_conflict("release_run_missing")
        run_evidence.require_execution_attempt(run, execution_attempt)
        status = run.get("status")
        if status not in {"running", "succeeded"}:
            raise release_conflict("release_run_terminal_conflict")
        if status == "succeeded":
            self._require_completed_result(conn, tool_record)
            return
        updated = self.repository.update_execution_run_status_if_budget(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ai_run_id=run_id,
            transition=AI_RUN_SUCCEEDED,
            expected_budget_json=run_evidence.budget(run),
            usage_json={"modelCallCount": 0, "toolCallCount": 1, "source": "governed_release_mcp"},
            error_json=None,
            completed_at=now,
        )
        if updated is None:
            raise release_conflict("release_execution_lease_lost")
        existing = self.repository.insert_tool_call_or_get_existing(transaction=conn, record=tool_record)
        if existing is not None:
            run_evidence.require_matching_tool_result(existing, tool_record)
        append_success_evidence(self.repository, self.audit, conn, ctx, binding, run_id, execution_attempt, now)

    def _require_completed_result(self, conn: TransactionContext, tool_record: AiToolCallRecord) -> None:
        existing = self.repository.insert_tool_call_or_get_existing(transaction=conn, record=tool_record)
        if existing is None:
            raise release_conflict("release_result_missing")
        run_evidence.require_matching_tool_result(existing, tool_record)

    def fail(
        self,
        ctx: RequestContext,
        run_id: str,
        binding: GovernedReleaseBinding,
        exc: Exception,
        execution_attempt: int = 0,
        *,
        is_known_not_committed: bool = False,
    ) -> None:
        now = _now()
        error = run_evidence.execution_error(ctx, exc, is_known_not_committed=is_known_not_committed)
        with self.engine.begin() as conn:
            run = self.repository.execution_run_by_id(transaction=conn, tenant_id=ctx.tenant_id, ai_run_id=run_id)
            if run is None or run.get("status") != "running" or recovery_attempt(run) != execution_attempt:
                return
            updated = self.repository.update_execution_run_status_if_budget(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                ai_run_id=run_id,
                transition=AI_RUN_FAILED,
                expected_budget_json=run_evidence.budget(run),
                usage_json={"modelCallCount": 0, "toolCallCount": 0, "source": "governed_release_mcp"},
                error_json=error,
                completed_at=now,
            )
            if updated is None:
                return
            append_failure_evidence(
                self.repository,
                self.audit,
                conn,
                ctx,
                binding,
                run_id,
                execution_attempt,
                error,
                now,
            )

    def defer(
        self,
        ctx: RequestContext,
        run_id: str,
        binding: GovernedReleaseBinding,
        exc: Exception,
        execution_attempt: int = 0,
    ) -> None:
        now = _now()
        with self.engine.begin() as conn:
            run = self.repository.execution_run_by_id(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                ai_run_id=run_id,
            )
            if run is None or run.get("status") != "running":
                return
            if recovery_attempt(run) != execution_attempt:
                return
            append_outcome_unknown_evidence(
                self.repository,
                self.audit,
                conn,
                ctx,
                binding,
                run_id,
                execution_attempt,
                type(exc).__name__,
                now,
            )

    def _consume_receipt(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        receipt_id: str,
        binding: GovernedReleaseBinding,
        now: str,
    ) -> None:
        receipt_id = widget_receipt_id(receipt_id)
        ledger = self.repository.ledger_for_run(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ai_run_id=receipt_id,
        )
        reason = receipt_conflict_reason(ledger, binding, now)
        if reason is not None:
            raise release_conflict(reason)
        updated = self.repository.update_execution_run_status(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ai_run_id=receipt_id,
            transition=AI_RUN_SUCCEEDED,
            usage_json={"source": "governed_release_mcp_widget_confirmation_consumed"},
            error_json=None,
            completed_at=now,
        )
        if updated is None:
            raise release_conflict("widget_confirmation_already_consumed")
        self.repository.append_execution_event(
            transaction=conn,
            record=event_record(
                ctx,
                receipt_id,
                2,
                "governed_release_widget_confirmation_consumed",
                {"requestBindingHash": binding.fingerprint},
                now,
            ),
        )

    def _create_session(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        binding: GovernedReleaseBinding,
        now: str,
    ) -> None:
        self.repository.create_session(
            transaction=conn,
            record=AiSessionRecord(
                id=binding.session_id,
                tenant_id=ctx.tenant_id,
                agent_version_id=f"governed-release-mcp:{binding.application_id}:v1",
                actor_user_id=ctx.actor_user_id,
                status="active",
                created_at=now,
                last_activity_at=now,
            ),
        )


__all__ = ["GovernedReleaseSecurityLedger"]
