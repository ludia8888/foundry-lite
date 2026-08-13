"""Pure identity and persistence contracts for Builder MCP widget approval."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.ports import AiExecutionRunRecord
from foundry_lite.application.services.aip.agent_runtime_ledger import hash_json
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import (
    FdeMcpRequestBinding,
    is_expired,
)
from foundry_lite.application.services.mcp_tool_results import serialized_text_content
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, PermissionDenied

_WIDGET_APPROVAL_RECORD_KIND = "builder_mcp_widget_approval_token"


@dataclass(frozen=True)
class FdeMcpWidgetApprovalBinding:
    """Identity facts that prevent a widget token crossing trust boundaries."""

    tenant_id: str
    actor_user_id: str
    application_id: str
    client_id: str
    oauth_session_id: str
    mcp_session_id: str
    challenge_id: str
    request_binding_hash: str
    origin: str

    @property
    def payload(self) -> dict[str, object]:
        """Return the stable identity payload persisted with the approval record."""

        return {
            "tenantId": self.tenant_id,
            "actorUserId": self.actor_user_id,
            "applicationId": self.application_id,
            "clientId": self.client_id,
            "oauthSessionId": self.oauth_session_id,
            "mcpSessionId": self.mcp_session_id,
            "challengeId": self.challenge_id,
            "requestBindingHash": self.request_binding_hash,
            "origin": self.origin,
        }

    @property
    def fingerprint(self) -> str:
        """Hash the complete approval identity for exact-boundary comparisons."""

        return hash_json(self.payload)


def can_issue_widget_token(ctx: RequestContext, application_id: str) -> bool:
    """Return whether the caller is an eligible human OAuth application principal."""

    is_machine = ctx.actor_user_id.startswith("service-principal:") or "osdk_service_principal" in ctx.roles
    return bool(
        not is_machine
        and ctx.application_id == application_id
        and ctx.client_id
        and ctx.oauth_session_id
        and ctx.token_scopes
    )


def widget_approval_binding(
    ctx: RequestContext,
    binding: FdeMcpRequestBinding,
    challenge_id: str,
    origin: str | None,
) -> FdeMcpWidgetApprovalBinding:
    """Build the trust-boundary identity bound to one widget approval challenge."""

    if not can_issue_widget_token(ctx, binding.application_id):
        raise PermissionDenied(
            "Builder MCP widget approval requires an authorization-code human application principal",
            details={"reason": "human_oauth_app_principal_required"},
        )
    return FdeMcpWidgetApprovalBinding(
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.actor_user_id,
        application_id=binding.application_id,
        client_id=str(ctx.client_id),
        oauth_session_id=str(ctx.oauth_session_id),
        mcp_session_id=binding.session_id,
        challenge_id=challenge_id,
        request_binding_hash=binding.fingerprint,
        origin=origin or "no-origin",
    )


def widget_token_id(secret: str) -> str:
    """Derive the non-secret durable ledger identifier for an approval secret."""

    digest = hash_json({"builderMcpWidgetApprovalSecret": secret}).removeprefix("sha256:")
    return f"aip_mcp_widget_token_{digest}"


def widget_token_record(
    ctx: RequestContext,
    binding: FdeMcpWidgetApprovalBinding,
    token_id: str,
    now: str,
    expires_at: str,
) -> AiExecutionRunRecord:
    """Create the durable one-time approval record stored in the AI run ledger."""

    budget = {
        "kind": _WIDGET_APPROVAL_RECORD_KIND,
        "widgetBinding": binding.payload,
        "widgetBindingHash": binding.fingerprint,
        "expiresAt": expires_at,
    }
    return AiExecutionRunRecord(
        id=token_id,
        tenant_id=ctx.tenant_id,
        session_id=binding.mcp_session_id,
        agent_version_id=f"builder-mcp:{binding.application_id}:v1",
        actor_user_id=ctx.actor_user_id,
        request_id=ctx.request_id,
        trace_id=ctx.request_id,
        status="running",
        ontology_version_id="active-ontology",
        model_alias_version="none",
        resolved_model_id="none",
        resolved_model_revision="none",
        prompt_version_id="builder-mcp-widget-confirmation-v1",
        compiled_prompt_hash=binding.fingerprint,
        tool_manifest_hash=hash_json(["approve_builder_mutation"]),
        context_manifest_hash=hash_json([binding.application_id, binding.mcp_session_id]),
        state_snapshot_hash=binding.fingerprint,
        policy_snapshot_hash=hash_json({"source": "builder_mcp_widget"}),
        budget_json=budget,
        usage_json=None,
        error_json=None,
        started_at=now,
        completed_at=None,
    )


def widget_token_conflict_reason(
    ledger: Mapping[str, object] | None,
    binding: FdeMcpWidgetApprovalBinding,
    now: str,
) -> str | None:
    """Explain why an approval record cannot be consumed, if it is invalid."""

    common_reason = _widget_token_common_conflict_reason(ledger, binding)
    if common_reason is not None:
        return common_reason
    run = ledger.get("run") if ledger is not None else None
    if not isinstance(run, Mapping):
        return "widget_token_invalid"
    budget = run.get("budget_json")
    if not isinstance(budget, Mapping):
        return "widget_token_invalid"
    if run.get("status") != "running":
        return "widget_token_already_consumed"
    if is_expired(budget.get("expiresAt"), now):
        return "widget_token_expired"
    return None


def widget_token_recovery_conflict_reason(
    ledger: Mapping[str, object] | None,
    binding: FdeMcpWidgetApprovalBinding,
) -> str | None:
    """Explain why a consumed approval record cannot recover its receipt."""

    common_reason = _widget_token_common_conflict_reason(ledger, binding)
    if common_reason is not None:
        return common_reason
    run = ledger.get("run") if ledger is not None else None
    if not isinstance(run, Mapping):
        return "widget_token_invalid"
    usage = run.get("usage_json")
    if run.get("status") != "succeeded" or not isinstance(usage, Mapping):
        return "widget_token_not_consumed"
    if usage.get("source") != "builder_mcp_widget_token_consumed":
        return "widget_token_not_consumed"
    return None


def _widget_token_common_conflict_reason(
    ledger: Mapping[str, object] | None,
    binding: FdeMcpWidgetApprovalBinding,
) -> str | None:
    """Validate record kind and caller binding shared by consume and recovery paths."""

    if ledger is None:
        return "widget_token_not_found"
    run = ledger.get("run")
    if not isinstance(run, Mapping):
        return "widget_token_invalid"
    budget = run.get("budget_json")
    if not isinstance(budget, Mapping) or budget.get("kind") != _WIDGET_APPROVAL_RECORD_KIND:
        return "widget_token_invalid"
    return None if _binding_matches(run, budget, binding) else "widget_token_binding_mismatch"


def widget_approval_payload(
    challenge_id: str,
    confirmation_receipt: str,
    expires_at: object,
) -> dict[str, object]:
    """Build the MCP response that keeps the raw receipt in private metadata."""

    structured = {"status": "approved", "challengeId": challenge_id, "expiresAt": expires_at}
    return {
        "structuredContent": structured,
        "content": serialized_text_content(structured),
        "isError": False,
        "_meta": {"confirmationReceipt": confirmation_receipt},
    }


def widget_binding_diagnostics(token_secret: str, expected_token_id: object) -> dict[str, object]:
    """Describe a token mismatch without echoing the secret back to the caller.

    A mismatch has two very different causes that look identical from outside: the host never
    handed the widget a token (so it approves with an empty string), or it handed over a token
    from a different challenge. Comparing derived ledger ids -- already non-secret digests, and
    only their tail -- separates the two without turning the error into an oracle.
    """

    observed = widget_token_id(token_secret) if token_secret else None
    return {
        "hasWidgetApprovalToken": bool(token_secret),
        "observedTokenIdSuffix": observed[-8:] if observed else None,
        "expectedTokenIdSuffix": str(expected_token_id)[-8:] if expected_token_id else None,
    }


def widget_conflict(reason: str, diagnostics: Mapping[str, object] | None = None) -> ConflictDetected:
    """Create the stable conflict envelope for an unusable widget approval.

    The reason is repeated in the message because hosts surface only the JSON-RPC message to
    the operator; ChatGPT drops `data.reason`, which left a failed approval indistinguishable
    from an expired one. The machine-readable copy stays in `details` for callers that read it.
    """

    details: dict[str, object] = {"reason": reason}
    summary = ""
    if diagnostics is not None:
        details.update(diagnostics)
        # Hosts show only the message, so the diagnosis has to ride along in it.
        summary = " (" + ", ".join(f"{key}={value}" for key, value in diagnostics.items()) + ")"
    return ConflictDetected(
        f"Builder MCP widget approval token cannot be used: {reason}{summary}",
        details=details,
    )


def _binding_matches(
    run: Mapping[str, object],
    budget: Mapping[str, object],
    binding: FdeMcpWidgetApprovalBinding,
) -> bool:
    """Return whether a ledger row remains bound to the exact approval identity."""

    return bool(
        run.get("actor_user_id") == binding.actor_user_id
        and run.get("compiled_prompt_hash") == binding.fingerprint
        and budget.get("widgetBindingHash") == binding.fingerprint
    )


__all__ = [
    "FdeMcpWidgetApprovalBinding",
    "can_issue_widget_token",
    "widget_approval_binding",
    "widget_approval_payload",
    "widget_conflict",
    "widget_token_conflict_reason",
    "widget_token_id",
    "widget_token_record",
    "widget_token_recovery_conflict_reason",
]
