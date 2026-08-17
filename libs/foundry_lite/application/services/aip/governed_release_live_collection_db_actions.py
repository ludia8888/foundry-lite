"""AI-ledger and issuer-authoritative identity validation for live collection."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import cast
from urllib.parse import unquote, urlsplit

from foundry_lite.application.ports.ai_run_repository import AiRunLedgerSnapshot
from foundry_lite.application.services.aip.fde_tool_result import hash_json
from foundry_lite.application.services.aip.governed_release_authorization import GOVERNED_RELEASE_SCOPE
from foundry_lite.application.services.aip.governed_release_live_collection_contract import (
    ReleaseKind,
    ReleaseTool,
    ServerActionClaim,
)
from foundry_lite.application.services.aip.governed_release_live_collection_db_types import (
    ActionLedgerSource,
    LoadedActionLedger,
    LoadedAuthBinding,
    SelectedAuditEvidence,
    ServerActionResultClaim,
    conflict,
    invalid,
    is_hash,
    is_text,
    required_row_text,
    row_time,
)

_OAUTH_SESSION_HASH = re.compile(r"^oauth-session:(sha256:[0-9a-f]{64})$")
_ACTION_KIND = "governed_release_mcp_action"


def loaded_action(
    ledger: AiRunLedgerSnapshot,
    tenant_id: str,
    application_id: str,
    source: ActionLedgerSource,
) -> LoadedActionLedger:
    run = ledger.get("run")
    if not isinstance(run, Mapping):
        invalid("action_ledger_run_invalid")
    started, completed = _require_run(run, tenant_id, application_id, source)
    binding, binding_hash = _request_binding(run, tenant_id, application_id, source, started)
    if ledger.get("modelCalls") != []:
        conflict("action_model_call_ledger_not_empty")
    result = _tool_result(ledger.get("toolCalls"), tenant_id, source, binding, started, completed)
    _require_audit_matches(source.audit, binding.actor_user_id, completed)
    claim = _action_claim(run, source, binding, binding_hash, started, completed)
    request_id = required_row_text(run, "request_id", "action_request_id_invalid")
    return LoadedActionLedger(
        claim,
        result,
        binding.actor_user_id,
        request_id,
        binding.policy_fingerprint,
        binding.authorization_policy,
    )


def require_action_identities(values: Sequence[LoadedActionLedger]) -> None:
    if len({value.policy_fingerprint for value in values}) != 1:
        conflict("action_authorization_policy_mismatch")
    submitters = _role_identities(values, is_submitter=True)
    reviewers = _role_identities(values, is_submitter=False)
    if len(submitters) != 1 or len(reviewers) != 1:
        conflict("action_principal_role_mismatch")
    submitter = next(iter(submitters))
    reviewer = next(iter(reviewers))
    if submitter[0] == reviewer[0]:
        conflict("action_submitter_reviewer_subject_overlap")
    if submitter[1] == reviewer[1]:
        conflict("action_submitter_reviewer_oauth_session_overlap")
    if _role_mcp_sessions(values, is_submitter=True) & _role_mcp_sessions(values, is_submitter=False):
        conflict("action_submitter_reviewer_mcp_session_overlap")


def _role_identities(
    values: Sequence[LoadedActionLedger],
    *,
    is_submitter: bool,
) -> set[tuple[str, str]]:
    return {
        (value.claim.actor_subject_hash, value.claim.oauth_session_hash)
        for value in values
        if (value.claim.tool_name == "publish_release_candidate") is is_submitter
    }


def _role_mcp_sessions(values: Sequence[LoadedActionLedger], *, is_submitter: bool) -> set[str]:
    return {
        value.claim.mcp_session_id
        for value in values
        if (value.claim.tool_name == "publish_release_candidate") is is_submitter
    }


def _action_claim(
    run: Mapping[str, object],
    source: ActionLedgerSource,
    binding: LoadedAuthBinding,
    binding_hash: str,
    started: datetime,
    completed: datetime,
) -> ServerActionClaim:
    return ServerActionClaim(
        source.release_kind,
        source.proposal_id,
        source.tool_name,
        source.ai_run_id,
        binding_hash,
        binding.policy_fingerprint,
        _subject_hash(run, binding),
        binding.oauth_session_hash,
        binding.session_id,
        binding.idempotency_key,
        "succeeded",
        started,
        completed,
    )


def _require_run(
    run: Mapping[str, object],
    tenant_id: str,
    application_id: str,
    source: ActionLedgerSource,
) -> tuple[datetime, datetime]:
    expected = (source.ai_run_id, tenant_id, f"governed-release-mcp:{application_id}:v1", "succeeded")
    observed = (run.get("id"), run.get("tenant_id"), run.get("agent_version_id"), run.get("status"))
    if observed != expected:
        conflict("action_ledger_scope_mismatch")
    started = row_time(run, "started_at")
    completed = row_time(run, "completed_at")
    if completed < started:
        invalid("action_run_timestamp_invalid")
    return started, completed


def _request_binding(
    run: Mapping[str, object],
    tenant_id: str,
    application_id: str,
    source: ActionLedgerSource,
    started: datetime,
) -> tuple[LoadedAuthBinding, str]:
    budget = run.get("budget_json")
    is_bounded = (
        budget.get("maxToolCalls") == 1 and budget.get("maxModelCalls") == 0 if isinstance(budget, Mapping) else False
    )
    if not isinstance(budget, Mapping) or budget.get("kind") != _ACTION_KIND or not is_bounded:
        invalid("action_request_binding_invalid")
    payload = budget.get("requestBinding")
    if not isinstance(payload, Mapping):
        invalid("action_request_binding_invalid")
    _require_binding_scope(payload, tenant_id, application_id, source)
    stored_hash = budget.get("requestBindingHash")
    if stored_hash != hash_json(payload) or not is_hash(stored_hash):
        conflict("action_request_binding_hash_mismatch")
    return _auth_binding(payload, application_id, source.tool_name, started), cast(str, stored_hash)


def _require_binding_scope(
    payload: Mapping[str, object],
    tenant_id: str,
    application_id: str,
    source: ActionLedgerSource,
) -> None:
    observed = (
        payload.get("tenantId"),
        payload.get("applicationId"),
        payload.get("releaseKind"),
        payload.get("proposalId"),
        payload.get("toolName"),
    )
    expected = (tenant_id, application_id, source.release_kind, source.proposal_id, source.tool_name)
    if observed != expected:
        conflict("action_request_binding_scope_mismatch")


def _auth_binding(
    payload: Mapping[str, object],
    application_id: str,
    tool: ReleaseTool,
    started: datetime,
) -> LoadedAuthBinding:
    texts = _binding_texts(payload)
    _require_auth_policy(payload, texts, application_id, tool, started)
    policy = _authorization_policy(application_id, texts)
    return LoadedAuthBinding(
        texts["actorUserId"],
        texts["sessionId"],
        _normalized_session_hash(texts["oauthSessionHash"]),
        texts["idempotencyKey"],
        texts["argumentsHash"],
        hash_json(policy),
        policy,
    )


def _authorization_policy(application_id: str, texts: Mapping[str, str]) -> dict[str, object]:
    return {
        "applicationId": application_id,
        "clientId": texts["clientId"],
        "authorizationServerIssuer": texts["authorizationServerIssuer"].rstrip("/"),
        "oauthGrantType": "authorization_code",
        "oauthResource": texts["oauthResource"],
        "oauthSessionAuthority": "issuer",
        "isHuman": True,
        "requiredScope": GOVERNED_RELEASE_SCOPE,
        "origin": texts["origin"].rstrip("/"),
    }


def _binding_texts(payload: Mapping[str, object]) -> dict[str, str]:
    keys = (
        "actorUserId clientId oauthSessionHash oauthSessionAuthority authorizationServerIssuer oauthGrantType "
        "oauthResource requiredScope sessionId argumentsHash idempotencyKey requiredPermission origin"
    ).split()
    values = {key: payload.get(key) for key in keys}
    if not all(is_text(value) for value in values.values()):
        invalid("action_auth_metadata_invalid")
    return {key: cast(str, value) for key, value in values.items()}


def _require_auth_policy(
    payload: Mapping[str, object],
    texts: Mapping[str, str],
    application_id: str,
    tool: ReleaseTool,
    started: datetime,
) -> None:
    expected = (
        texts["oauthSessionAuthority"] == "issuer",
        texts["oauthGrantType"] == "authorization_code",
        texts["requiredScope"] == GOVERNED_RELEASE_SCOPE,
        payload.get("isHuman") is True,
        texts["requiredPermission"] == _required_permission(tool, cast(ReleaseKind, payload.get("releaseKind"))),
        is_hash(texts["argumentsHash"]),
        _is_https_endpoint(texts["authorizationServerIssuer"], is_origin=False),
        _is_release_resource(texts["oauthResource"], application_id),
        texts["origin"].rstrip("/") == "https://chatgpt.com",
        _valid_token_window(payload, started),
    )
    if not all(expected):
        invalid("action_auth_metadata_invalid")


def _tool_result(
    values: object,
    tenant_id: str,
    source: ActionLedgerSource,
    binding: LoadedAuthBinding,
    started: datetime,
    completed: datetime,
) -> ServerActionResultClaim:
    row = _single_tool_result(values)
    _require_tool_result_binding(row, tenant_id, source, binding)
    result, fingerprint = _verified_result_json(row)
    _require_tool_times(row, started, completed)
    return ServerActionResultClaim(
        source.release_kind,
        source.tool_name,
        source.ai_run_id,
        fingerprint,
        result,
    )


def _single_tool_result(values: object) -> Mapping[str, object]:
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], Mapping):
        conflict("action_tool_call_count_mismatch")
    return cast(Mapping[str, object], values[0])


def _require_tool_result_binding(
    row: Mapping[str, object],
    tenant_id: str,
    source: ActionLedgerSource,
    binding: LoadedAuthBinding,
) -> None:
    expected = (tenant_id, source.ai_run_id, 1, source.tool_name, "WRITE", "succeeded")
    observed = (
        row.get("tenant_id"),
        row.get("ai_run_id"),
        row.get("sequence"),
        row.get("tool_id"),
        row.get("effect"),
        row.get("status"),
    )
    if observed != expected or row.get("arguments_hash") != binding.arguments_hash:
        conflict("action_tool_call_binding_mismatch")
    is_authorized = (
        row.get("id") == f"{source.ai_run_id}-tool-1"
        and row.get("tool_version") == "v1"
        and row.get("authorization_decision") == "allowed_by_widget_human_oauth_confirmation"
        and row.get("confirmation_policy") == "USER"
        and row.get("linked_action_run_id") is None
        and row.get("error_json") is None
    )
    if not is_authorized:
        invalid("action_tool_authorization_invalid")


def _verified_result_json(row: Mapping[str, object]) -> tuple[dict[str, object], str]:
    result = row.get("result_json")
    if not isinstance(result, Mapping):
        invalid("action_result_json_invalid")
    fingerprint = hash_json(result)
    if row.get("result_hash") != fingerprint or not is_hash(fingerprint):
        conflict("action_result_fingerprint_mismatch")
    copied = json.loads(json.dumps(dict(result), ensure_ascii=False, sort_keys=True))
    if not isinstance(copied, dict):
        invalid("action_result_json_invalid")
    return cast(dict[str, object], copied), fingerprint


def _require_tool_times(row: Mapping[str, object], started: datetime, completed: datetime) -> None:
    tool_started = row_time(row, "started_at")
    tool_completed = row_time(row, "completed_at")
    if not (started <= tool_started <= tool_completed <= completed):
        invalid("action_tool_timestamp_invalid")


def _require_audit_matches(audit: SelectedAuditEvidence | None, actor: str, completed: datetime) -> None:
    if audit is not None and (audit.actor_user_id != actor or audit.created_at < completed):
        conflict("action_audit_ledger_mismatch")


def _subject_hash(run: Mapping[str, object], binding: LoadedAuthBinding) -> str:
    actor = required_row_text(run, "actor_user_id", "action_actor_invalid")
    if actor != binding.actor_user_id or run.get("session_id") != binding.session_id:
        conflict("action_principal_binding_mismatch")
    budget = cast(Mapping[str, object], run.get("budget_json"))
    request = cast(Mapping[str, object], budget.get("requestBinding"))
    issuer = cast(str, request.get("authorizationServerIssuer")).rstrip("/")
    return hash_json({"issuer": issuer, "subject": actor})


def _required_permission(tool: ReleaseTool, kind: ReleaseKind) -> str:
    if tool == "publish_release_candidate":
        return "pipeline:write" if kind == "pipeline" else "ontology:validate"
    if tool in {"assign_release_reviewer", "submit_release_decision"}:
        return "pipeline:review" if kind == "pipeline" else "ontology:activate"
    return "pipeline:deploy" if kind == "pipeline" else "ontology:activate"


def _valid_token_window(payload: Mapping[str, object], started: datetime) -> bool:
    issued = payload.get("oauthTokenIssuedAt")
    expires = payload.get("oauthTokenExpiresAt")
    valid_types = not isinstance(issued, bool) and not isinstance(expires, bool)
    if not valid_types or not isinstance(issued, int) or not isinstance(expires, int):
        return False
    timestamp = int(started.timestamp())
    return 0 < issued <= timestamp <= expires and expires > issued


def _is_release_resource(value: str, application_id: str) -> bool:
    parsed = urlsplit(value)
    return _is_https_endpoint(value, is_origin=False) and unquote(parsed.path) == f"/mcp/release/{application_id}"


def _is_https_endpoint(value: str, *, is_origin: bool) -> bool:
    parsed = urlsplit(value)
    clean = bool(
        parsed.scheme == "https"
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )
    return clean and (not is_origin or parsed.path in {"", "/"})


def _normalized_session_hash(value: str) -> str:
    match = _OAUTH_SESSION_HASH.fullmatch(value)
    if match is None:
        invalid("action_oauth_session_hash_invalid")
    return match.group(1)


__all__ = ["loaded_action", "require_action_identities"]
