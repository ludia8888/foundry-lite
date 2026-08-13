"""Transactional ledger for one-time Builder MCP widget approval tokens."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import (
    AiRunRepository,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.ports.transaction_context import AI_RUN_CHALLENGE_REFRESHED, AI_RUN_SUCCEEDED
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.aip.agent_runtime_ledger import event_record, hash_json
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import (
    FdeMcpRequestBinding,
    challenge_binding,
    expires_at,
    receipt_conflict_reason,
    receipt_record,
)
from foundry_lite.application.services.aip.fde_mcp_widget_confirmation_contract import (
    FdeMcpWidgetApprovalBinding,
    can_issue_widget_token,
    widget_approval_binding,
    widget_approval_payload,
    widget_binding_diagnostics,
    widget_conflict,
    widget_token_conflict_reason,
    widget_token_id,
    widget_token_record,
    widget_token_recovery_conflict_reason,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.security.policy import PolicyService


class FdeMcpWidgetConfirmationLedger:
    """Issue and atomically consume app-only Builder approval tokens."""

    repository: AiRunRepository

    def __init__(self, engine: TransactionManager, repository: AiRunRepository, policy: PolicyService) -> None:
        self.engine = engine
        self.repository = repository
        self.policy = policy

    def issue_in_transaction(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        binding: FdeMcpRequestBinding,
        challenge_id: str,
        now: str,
        token_expires_at: str,
    ) -> str | None:
        if not can_issue_widget_token(ctx, binding.application_id):
            return None
        return self._issue_and_bind_token(conn, ctx, binding, challenge_id, now, token_expires_at)

    def rotate_in_transaction(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        binding: FdeMcpRequestBinding,
        challenge_id: str,
        challenge: Mapping[str, object],
        now: str,
        token_expires_at: str,
    ) -> str | None:
        if not can_issue_widget_token(ctx, binding.application_id):
            return None
        self.revoke_active_in_transaction(conn, ctx, challenge, now, "replaced_by_exact_replay")
        return self._issue_and_bind_token(conn, ctx, binding, challenge_id, now, token_expires_at)

    def _issue_and_bind_token(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        binding: FdeMcpRequestBinding,
        challenge_id: str,
        now: str,
        token_expires_at: str,
    ) -> str:
        secret = _new_id("aip_mcp_widget_secret")
        widget_binding = widget_approval_binding(ctx, binding, challenge_id, binding.origin)
        token_id = widget_token_id(secret)
        self.repository.create_execution_run(
            transaction=conn,
            record=widget_token_record(ctx, widget_binding, token_id, now, token_expires_at),
        )
        self.repository.append_execution_event(
            transaction=conn,
            record=event_record(
                ctx,
                token_id,
                1,
                "builder_mcp_widget_token_issued",
                {"challengeId": challenge_id, "expiresAt": token_expires_at},
                now,
            ),
        )
        updated = self.repository.update_execution_run_status(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ai_run_id=challenge_id,
            transition=AI_RUN_CHALLENGE_REFRESHED,
            usage_json={"source": "builder_mcp_widget_token_active", "widgetTokenId": token_id},
            error_json=None,
            completed_at=None,
        )
        if updated is None:
            raise widget_conflict("widget_token_rotation_conflict")
        return secret

    def revoke_active_in_transaction(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        challenge: Mapping[str, object],
        now: str,
        reason: str,
    ) -> None:
        run = challenge.get("run")
        usage = run.get("usage_json") if isinstance(run, Mapping) else None
        token_id = usage.get("widgetTokenId") if isinstance(usage, Mapping) else None
        if not isinstance(token_id, str):
            return
        updated = self.repository.update_execution_run_status(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ai_run_id=token_id,
            transition=AI_RUN_SUCCEEDED,
            usage_json={"source": "builder_mcp_widget_token_revoked", "reason": reason},
            error_json=None,
            completed_at=now,
        )
        if updated is None:
            raise widget_conflict("widget_token_rotation_conflict")
        self.repository.append_execution_event(
            transaction=conn,
            record=event_record(ctx, token_id, 2, "builder_mcp_widget_token_revoked", {"reason": reason}, now),
        )

    def approve(
        self,
        ctx: RequestContext,
        application_id: str,
        mcp_session_id: str,  # noqa: ARG002 - transport identity, kept for audit, not authorization
        challenge_id: str,
        token_secret: str,
        origin: str | None,
    ) -> dict[str, object]:
        now = _now()
        receipt_id = _new_id("aip_mcp_receipt")
        receipt_expires_at = expires_at(now)
        with self.engine.begin() as conn:
            ledger = self.repository.ledger_for_run(transaction=conn, tenant_id=ctx.tenant_id, ai_run_id=challenge_id)
            binding = challenge_binding(ledger, application_id, now)
            # The MCP session is a transport identifier, not an authorization boundary. A host
            # that renders the approval card in its own frame approves from a second session, so
            # requiring the challenge's session rejected every real widget approval. The identity
            # that matters -- OAuth session, actor, tenant, application, client, challenge,
            # request hash, origin -- is still enforced through the approval fingerprint.
            widget_binding = widget_approval_binding(ctx, binding, challenge_id, origin)
            self.policy.require(ctx, "aip:mcp:confirm")
            self.policy.require(ctx, binding.required_permission)
            recovered = self._recover_receipt(conn, ctx, ledger, binding, widget_binding, token_secret, now)
            if recovered is not None:
                return widget_approval_payload(challenge_id, recovered[0], recovered[1])
            self._require_active_token(ledger, token_secret)
            self._consume_token(conn, ctx, token_secret, widget_binding, now)
            self._approve_challenge(
                conn,
                ctx,
                challenge_id,
                receipt_id,
                widget_token_id(token_secret),
                binding,
                now,
                receipt_expires_at,
            )
        return widget_approval_payload(challenge_id, receipt_id, receipt_expires_at)

    def is_recovery(
        self,
        ctx: RequestContext,
        application_id: str,
        mcp_session_id: str,  # noqa: ARG002 - transport identity, kept for audit, not authorization
        challenge_id: str,
        token_secret: str,
        origin: str | None,
    ) -> bool:
        now = _now()
        with self.engine.begin() as conn:
            ledger = self.repository.ledger_for_run(transaction=conn, tenant_id=ctx.tenant_id, ai_run_id=challenge_id)
            binding = challenge_binding(ledger, application_id, now)
            # The MCP session is a transport identifier, not an authorization boundary. A host
            # that renders the approval card in its own frame approves from a second session, so
            # requiring the challenge's session rejected every real widget approval. The identity
            # that matters -- OAuth session, actor, tenant, application, client, challenge,
            # request hash, origin -- is still enforced through the approval fingerprint.
            widget_binding = widget_approval_binding(ctx, binding, challenge_id, origin)
            self.policy.require(ctx, "aip:mcp:confirm")
            self.policy.require(ctx, binding.required_permission)
            recovered = self._recover_receipt(conn, ctx, ledger, binding, widget_binding, token_secret, now)
            if recovered is not None:
                return True
            self._require_active_token(ledger, token_secret)
            self._validate_active_token(conn, ctx, token_secret, widget_binding, now)
        return False

    def recover_receipt_in_transaction(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        challenge: Mapping[str, object],
        binding: FdeMcpRequestBinding,
        challenge_id: str,
        now: str,
    ) -> tuple[str, object] | None:
        references = _receipt_recovery_references(challenge)
        if references is None:
            return None
        token_id, receipt_id = references
        widget_binding = widget_approval_binding(ctx, binding, challenge_id, binding.origin)
        token = self.repository.ledger_for_run(transaction=conn, tenant_id=ctx.tenant_id, ai_run_id=token_id)
        _require_no_widget_conflict(widget_token_recovery_conflict_reason(token, widget_binding))
        receipt = self.repository.ledger_for_run(transaction=conn, tenant_id=ctx.tenant_id, ai_run_id=receipt_id)
        _require_no_widget_conflict(receipt_conflict_reason(receipt, binding, now))
        return receipt_id, _receipt_expiration(receipt)

    def _recover_receipt(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        challenge: Mapping[str, object] | None,
        binding: FdeMcpRequestBinding,
        widget_binding: FdeMcpWidgetApprovalBinding,
        token_secret: str,
        now: str,
    ) -> tuple[str, object] | None:
        if not isinstance(challenge, Mapping):
            return None
        recovered = self.recover_receipt_in_transaction(conn, ctx, challenge, binding, widget_binding.challenge_id, now)
        if recovered is None:
            return None
        token_id = widget_token_id(token_secret)
        run = challenge.get("run")
        usage = run.get("usage_json") if isinstance(run, Mapping) else None
        if not isinstance(usage, Mapping) or usage.get("widgetTokenId") != token_id:
            raise widget_conflict("widget_token_binding_mismatch")
        token = self.repository.ledger_for_run(transaction=conn, tenant_id=ctx.tenant_id, ai_run_id=token_id)
        reason = widget_token_recovery_conflict_reason(token, widget_binding)
        if reason is not None:
            raise widget_conflict(reason)
        return recovered

    def _require_active_token(self, challenge: Mapping[str, object] | None, token_secret: str) -> None:
        run = challenge.get("run") if isinstance(challenge, Mapping) else None
        usage = run.get("usage_json") if isinstance(run, Mapping) else None
        active_token_id = usage.get("widgetTokenId") if isinstance(usage, Mapping) else None
        if not isinstance(run, Mapping):
            raise widget_conflict("widget_token_binding_mismatch")
        if run.get("status") == "started" and active_token_id != widget_token_id(token_secret):
            raise widget_conflict(
                "widget_token_binding_mismatch",
                widget_binding_diagnostics(token_secret, active_token_id),
            )

    def _consume_token(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        token_secret: str,
        binding: FdeMcpWidgetApprovalBinding,
        now: str,
    ) -> None:
        token_id = widget_token_id(token_secret)
        self._validate_active_token(conn, ctx, token_secret, binding, now)
        updated = self.repository.update_execution_run_status(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ai_run_id=token_id,
            transition=AI_RUN_SUCCEEDED,
            usage_json={"source": "builder_mcp_widget_token_consumed"},
            error_json=None,
            completed_at=now,
        )
        if updated is None:
            raise widget_conflict("widget_token_already_consumed")
        self.repository.append_execution_event(
            transaction=conn,
            record=event_record(
                ctx,
                token_id,
                2,
                "builder_mcp_widget_token_consumed",
                {"challengeId": binding.challenge_id, "widgetBindingHash": binding.fingerprint},
                now,
            ),
        )

    def _validate_active_token(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        token_secret: str,
        binding: FdeMcpWidgetApprovalBinding,
        now: str,
    ) -> None:
        token_id = widget_token_id(token_secret)
        ledger = self.repository.ledger_for_run(transaction=conn, tenant_id=ctx.tenant_id, ai_run_id=token_id)
        reason = widget_token_conflict_reason(ledger, binding, now)
        if reason is not None:
            raise widget_conflict(reason)

    def _approve_challenge(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        challenge_id: str,
        receipt_id: str,
        widget_token_id_value: str,
        binding: FdeMcpRequestBinding,
        now: str,
        receipt_expires_at: str,
    ) -> None:
        updated = self.repository.update_execution_run_status(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ai_run_id=challenge_id,
            transition=AI_RUN_SUCCEEDED,
            usage_json={
                "source": "builder_mcp_widget_approval",
                "confirmationReceipt": receipt_id,
                "receiptExpiresAt": receipt_expires_at,
                "approvedByUserId": ctx.actor_user_id,
                "widgetTokenId": widget_token_id_value,
            },
            error_json=None,
            completed_at=now,
        )
        if updated is None:
            raise widget_conflict("challenge_already_approved")
        self._record_approval(conn, ctx, challenge_id, receipt_id, binding, now, receipt_expires_at)

    def _record_approval(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        challenge_id: str,
        receipt_id: str,
        binding: FdeMcpRequestBinding,
        now: str,
        receipt_expires_at: str,
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
            record=receipt_record(ctx, binding, receipt_id, challenge_id, now, receipt_expires_at),
        )
        self.repository.append_execution_event(
            transaction=conn,
            record=event_record(
                ctx,
                receipt_id,
                1,
                "mcp_confirmation_receipt_issued",
                {"challengeId": challenge_id, "approvedByUserId": ctx.actor_user_id},
                now,
            ),
        )


def _receipt_recovery_references(challenge: Mapping[str, object]) -> tuple[str, str] | None:
    run = challenge.get("run")
    if not isinstance(run, Mapping) or run.get("status") != "succeeded":
        return None
    usage = run.get("usage_json")
    if not isinstance(usage, Mapping):
        return None
    token_id = usage.get("widgetTokenId")
    receipt_id = usage.get("confirmationReceipt")
    if not isinstance(token_id, str) or not isinstance(receipt_id, str):
        return None
    return token_id, receipt_id


def _receipt_expiration(receipt: Mapping[str, object] | None) -> object:
    receipt_run = receipt.get("run") if isinstance(receipt, Mapping) else None
    budget = receipt_run.get("budget_json") if isinstance(receipt_run, Mapping) else None
    return budget.get("expiresAt") if isinstance(budget, Mapping) else None


def _require_no_widget_conflict(reason: str | None) -> None:
    if reason is not None:
        raise widget_conflict(reason)


__all__ = [
    "FdeMcpWidgetApprovalBinding",
    "FdeMcpWidgetConfirmationLedger",
    "can_issue_widget_token",
    "widget_approval_binding",
    "widget_approval_payload",
    "widget_conflict",
    "widget_token_conflict_reason",
    "widget_token_id",
    "widget_token_record",
    "widget_token_recovery_conflict_reason",
]
