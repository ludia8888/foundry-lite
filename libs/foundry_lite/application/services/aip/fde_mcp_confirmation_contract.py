"""Pure identity, expiry, and persistence records for Builder MCP confirmation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from foundry_lite.application.ports import AiExecutionRunRecord
from foundry_lite.application.services.aip.agent_runtime_ledger import hash_json
from foundry_lite.application.services.mcp_tool_results import serialized_text_content
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, PermissionDenied

JsonObject = Mapping[str, object]
_CONFIRMATION_TTL_SECONDS = 300
_CHALLENGE_KIND = "builder_mcp_confirmation_challenge"
_RECEIPT_KIND = "builder_mcp_confirmation_receipt"


@dataclass(frozen=True)
class FdeMcpRequestBinding:
    tenant_id: str
    actor_user_id: str
    application_id: str
    client_id: str
    oauth_session_id: str
    session_id: str
    tool_id: str
    mode: str
    workspace_ref: str
    arguments_hash: str
    required_permission: str
    origin: str

    @property
    def payload(self) -> dict[str, object]:
        return {
            "tenantId": self.tenant_id,
            "actorUserId": self.actor_user_id,
            "applicationId": self.application_id,
            "clientId": self.client_id,
            "oauthSessionId": self.oauth_session_id,
            "sessionId": self.session_id,
            "toolId": self.tool_id,
            "mode": self.mode,
            "workspaceRef": self.workspace_ref,
            "argumentsHash": self.arguments_hash,
            "requiredPermission": self.required_permission,
            "origin": self.origin,
        }

    @property
    def fingerprint(self) -> str:
        return hash_json(self.payload)

    def as_recorded_session(self, budget: Mapping[str, object]) -> FdeMcpRequestBinding:
        """Re-key this binding onto the MCP session the challenge was recorded under.

        Approval leaves the originating session by design: an MCP Apps host renders the card in
        its own frame and follows up from a second session, exactly as Foundry surfaces an agent
        proposal on a separate review surface. Identity still has to match, so only the transport
        session is normalised here -- actor, application, client, OAuth session, arguments and
        origin are all still compared through the fingerprint.
        """

        recorded = budget.get("requestBinding")
        session_id = recorded.get("sessionId") if isinstance(recorded, Mapping) else None
        if not isinstance(session_id, str) or session_id == self.session_id:
            return self
        return replace(self, session_id=session_id)


@dataclass(frozen=True)
class FdeMcpReplay:
    tool_call_id: str
    output: Mapping[str, object]
    is_error: bool = False


def request_binding(
    ctx: RequestContext,
    *,
    application_id: str,
    session_id: str,
    tool_id: str,
    mode: str,
    workspace_ref: str,
    arguments: JsonObject,
    required_permission: str,
    origin: str | None,
) -> FdeMcpRequestBinding:
    return FdeMcpRequestBinding(
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.actor_user_id,
        application_id=application_id,
        client_id=ctx.client_id or "",
        oauth_session_id=ctx.oauth_session_id or "",
        session_id=session_id,
        tool_id=tool_id,
        mode=mode,
        workspace_ref=workspace_ref,
        arguments_hash=hash_json(arguments),
        required_permission=required_permission,
        origin=origin or "no-origin",
    )


def execution_binding_budget(binding: FdeMcpRequestBinding) -> dict[str, object]:
    return {
        "maxToolCalls": 1,
        "maxModelCalls": 0,
        "requestBinding": binding.payload,
        "requestBindingHash": binding.fingerprint,
    }


def challenge_record(
    ctx: RequestContext,
    binding: FdeMcpRequestBinding,
    challenge_id: str,
    now: str,
    expires_at: str,
) -> AiExecutionRunRecord:
    budget = {
        "kind": _CHALLENGE_KIND,
        "requestBinding": binding.payload,
        "requestBindingHash": binding.fingerprint,
        "expiresAt": expires_at,
    }
    return _security_run_record(ctx, binding, challenge_id, "started", now, budget)


def receipt_record(
    ctx: RequestContext,
    binding: FdeMcpRequestBinding,
    receipt_id: str,
    challenge_id: str,
    now: str,
    expires_at: str,
) -> AiExecutionRunRecord:
    budget = {
        "kind": _RECEIPT_KIND,
        "challengeId": challenge_id,
        "approvedByUserId": ctx.actor_user_id,
        "requestBinding": binding.payload,
        "requestBindingHash": binding.fingerprint,
        "expiresAt": expires_at,
    }
    return _security_run_record(ctx, binding, receipt_id, "running", now, budget)


def _security_run_record(
    ctx: RequestContext,
    binding: FdeMcpRequestBinding,
    run_id: str,
    status: str,
    now: str,
    budget: JsonObject,
) -> AiExecutionRunRecord:
    return AiExecutionRunRecord(
        id=run_id,
        tenant_id=ctx.tenant_id,
        session_id=binding.session_id,
        agent_version_id=f"builder-mcp:{binding.application_id}:v1",
        actor_user_id=binding.actor_user_id,
        request_id=ctx.request_id,
        trace_id=ctx.request_id,
        status=status,
        ontology_version_id="active-ontology",
        model_alias_version="none",
        resolved_model_id="none",
        resolved_model_revision="none",
        prompt_version_id="builder-mcp-confirmation-v1",
        compiled_prompt_hash=binding.fingerprint,
        tool_manifest_hash=hash_json([binding.tool_id]),
        context_manifest_hash=hash_json([binding.workspace_ref]),
        state_snapshot_hash=binding.fingerprint,
        policy_snapshot_hash=hash_json({"requiredPermission": binding.required_permission}),
        budget_json=budget,
        usage_json=None,
        error_json=None,
        started_at=now,
        completed_at=None,
    )


def challenge_binding(
    ledger: Mapping[str, object] | None,
    application_id: str,
    now: str,
) -> FdeMcpRequestBinding:
    if ledger is None:
        raise confirmation_conflict("challenge_not_found")
    run = ledger.get("run")
    if not isinstance(run, Mapping) or run.get("status") not in {"started", "succeeded"}:
        raise confirmation_conflict("challenge_invalid")
    budget = run.get("budget_json")
    if not isinstance(budget, Mapping) or budget.get("kind") != _CHALLENGE_KIND:
        raise confirmation_conflict("challenge_invalid")
    if run.get("status") == "started" and is_expired(budget.get("expiresAt"), now):
        raise confirmation_conflict("challenge_expired")
    binding = _binding_from_budget(budget)
    if binding.application_id != application_id:
        raise confirmation_conflict("challenge_application_mismatch")
    if not binding_matches(run, binding):
        raise confirmation_conflict("challenge_binding_invalid")
    return binding


def challenge_replay_conflict_reason(
    ledger: Mapping[str, object],
    binding: FdeMcpRequestBinding,
    now: str,
) -> str | None:
    run = ledger.get("run")
    if not isinstance(run, Mapping):
        return "challenge_invalid"
    budget = run.get("budget_json")
    if not isinstance(budget, Mapping) or budget.get("kind") != _CHALLENGE_KIND:
        return "challenge_invalid"
    comparable = binding.as_recorded_session(budget)
    if budget.get("requestBindingHash") != comparable.fingerprint or not binding_matches(run, comparable):
        return "challenge_binding_mismatch"
    if run.get("status") not in {"started", "succeeded"}:
        return "challenge_invalid"
    if run.get("status") == "started" and is_expired(budget.get("expiresAt"), now):
        return "challenge_expired"
    return None


def existing_challenge_payload(
    ledger: Mapping[str, object],
    challenge_id: str,
    binding: FdeMcpRequestBinding,
) -> dict[str, object]:
    run = ledger.get("run")
    budget = run.get("budget_json") if isinstance(run, Mapping) else None
    expires_at = budget.get("expiresAt") if isinstance(budget, Mapping) else None
    payload = challenge_payload(challenge_id, binding, str(expires_at))
    payload["isReplayed"] = True
    return payload


def receipt_conflict_reason(
    ledger: Mapping[str, object] | None,
    binding: FdeMcpRequestBinding,
    now: str,
) -> str | None:
    if ledger is None:
        return "receipt_not_found"
    run = ledger.get("run")
    if not isinstance(run, Mapping):
        return "receipt_invalid"
    budget = run.get("budget_json")
    if not isinstance(budget, Mapping) or budget.get("kind") != _RECEIPT_KIND:
        return "receipt_invalid"
    if run.get("status") != "running":
        return "receipt_already_consumed"
    if is_expired(budget.get("expiresAt"), now):
        return "receipt_expired"
    comparable = binding.as_recorded_session(budget)
    is_bound = budget.get("requestBindingHash") == comparable.fingerprint and binding_matches(run, comparable)
    return None if is_bound else "receipt_binding_mismatch"


def _binding_from_budget(budget: Mapping[str, object]) -> FdeMcpRequestBinding:
    payload = budget.get("requestBinding")
    if not isinstance(payload, Mapping):
        raise confirmation_conflict("challenge_binding_missing")
    try:
        values = {
            "tenant_id": payload["tenantId"],
            "actor_user_id": payload["actorUserId"],
            "application_id": payload["applicationId"],
            "client_id": payload["clientId"],
            "oauth_session_id": payload.get("oauthSessionId", ""),
            "session_id": payload["sessionId"],
            "tool_id": payload["toolId"],
            "mode": payload["mode"],
            "workspace_ref": payload["workspaceRef"],
            "arguments_hash": payload["argumentsHash"],
            "required_permission": payload["requiredPermission"],
            "origin": payload.get("origin", "no-origin"),
        }
    except KeyError as exc:
        raise confirmation_conflict("challenge_binding_invalid") from exc
    if not all(isinstance(value, str) for value in values.values()):
        raise confirmation_conflict("challenge_binding_invalid")
    required = values.keys() - {"client_id", "oauth_session_id"}
    if any(not values[name] for name in required):
        raise confirmation_conflict("challenge_binding_invalid")
    return FdeMcpRequestBinding(**values)


def binding_matches(run: Mapping[str, object], binding: FdeMcpRequestBinding) -> bool:
    budget = run.get("budget_json")
    return (
        run.get("actor_user_id") == binding.actor_user_id
        and run.get("compiled_prompt_hash") == binding.fingerprint
        and isinstance(budget, Mapping)
        and budget.get("requestBindingHash") == binding.fingerprint
    )


def terminal_replay(calls: list[Mapping[str, object]]) -> FdeMcpReplay | None:
    if not calls:
        return None
    output = calls[0].get("result_json")
    tool_call_id = calls[0].get("id")
    if not isinstance(output, Mapping) or not isinstance(tool_call_id, str):
        return None
    return FdeMcpReplay(tool_call_id=tool_call_id, output=output)


def failed_replay(run: Mapping[str, object]) -> FdeMcpReplay | None:
    error = run.get("error_json")
    structured = error.get("mcpToolResult") if isinstance(error, Mapping) else None
    run_id = run.get("id")
    if not isinstance(structured, Mapping) or not isinstance(run_id, str):
        return None
    return FdeMcpReplay(
        tool_call_id=f"{run_id}-tool-1",
        output=structured,
        is_error=True,
    )


def challenge_payload(
    challenge_id: str,
    binding: FdeMcpRequestBinding,
    expires_at: str,
) -> dict[str, object]:
    structured: dict[str, object] = {
        "status": "approval_required",
        "challengeId": challenge_id,
        "toolId": binding.tool_id,
        "mode": binding.mode,
        "workspaceRef": binding.workspace_ref,
        "requestBindingHash": binding.fingerprint,
        "expiresAt": expires_at,
    }
    return {
        "structuredContent": structured,
        "content": serialized_text_content(structured),
        "isError": False,
        "isReplayed": False,
    }


def challenge_id(run_id: str) -> str:
    return f"aip_mcp_confirmation_{hash_json({'executionRunId': run_id})[:32]}"


def require_human_control_principal(ctx: RequestContext) -> None:
    if ctx.application_id is not None or ctx.client_id is not None:
        raise PermissionDenied(
            "Builder MCP confirmation requires a human control-plane principal",
            details={"reason": "human_control_principal_required"},
        )


def expires_at(now: str) -> str:
    return (_parse_time(now) + timedelta(seconds=_CONFIRMATION_TTL_SECONDS)).isoformat()


def is_expired(value: object, now: str) -> bool:
    if not isinstance(value, str):
        return True
    try:
        return _parse_time(value) <= _parse_time(now)
    except ValueError:
        return True


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def next_sequence(events: list[Mapping[str, object]]) -> int:
    sequences = [row.get("sequence") for row in events]
    return max((value for value in sequences if isinstance(value, int)), default=0) + 1


def replay_conflict(run_id: str, reason: str) -> ConflictDetected:
    return ConflictDetected(
        "Builder MCP JSON-RPC id is already bound to a different or non-terminal request",
        details={"reason": reason, "aiRunId": run_id},
    )


def confirmation_conflict(reason: str) -> ConflictDetected:
    """Name the reason in the message; hosts show only that and drop `data.reason`."""

    return ConflictDetected(
        f"Builder MCP confirmation challenge or receipt cannot be used: {reason}",
        details={"reason": reason},
    )
