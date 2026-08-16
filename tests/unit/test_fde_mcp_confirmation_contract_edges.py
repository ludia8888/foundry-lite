"""Corrupt-ledger and exact-binding proof for Builder MCP confirmation."""

from __future__ import annotations

from dataclasses import asdict, replace

import pytest
from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import (
    FdeMcpRequestBinding,
    binding_matches,
    challenge_binding,
    challenge_record,
    challenge_replay_conflict_reason,
    execution_binding_budget,
    failed_replay,
    is_expired,
    next_sequence,
    receipt_conflict_reason,
    receipt_record,
    request_binding,
    require_human_control_principal,
    terminal_replay,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, PermissionDenied

_NOW = "2026-08-13T00:00:00+00:00"
_EXPIRES = "2026-08-13T00:05:00+00:00"


def _context(*, application_id: str | None = "app-a", client_id: str | None = "client-a") -> RequestContext:
    return RequestContext(
        tenant_id="tenant-a",
        actor_user_id="human-a",
        roles=("data_engineer",),
        application_id=application_id,
        client_id=client_id,
        token_scopes=("osdk:connector:fde_ontology_editing:execute",),
        oauth_session_id="oauth-a",
        request_id="request-a",
    )


def _binding() -> FdeMcpRequestBinding:
    return request_binding(
        _context(),
        application_id="app-a",
        session_id="mcp-a",
        tool_id="ontology.branch.apply_patch",
        mode="ontology_editing",
        workspace_ref="ontology-branch:branch-a",
        arguments={"patch": [{"op": "add"}]},
        required_permission="ontology:write",
        origin="https://chatgpt.com",
    )


def _challenge_ledger(binding: FdeMcpRequestBinding | None = None) -> dict[str, object]:
    selected = binding or _binding()
    return {"run": asdict(challenge_record(_context(), selected, "challenge-a", _NOW, _EXPIRES))}


def _receipt_ledger(binding: FdeMcpRequestBinding | None = None) -> dict[str, object]:
    selected = binding or _binding()
    return {"run": asdict(receipt_record(_context(), selected, "receipt-a", "challenge-a", _NOW, _EXPIRES))}


def test_challenge_binding_requires_all_persisted_hash_and_actor_evidence() -> None:
    binding = _binding()
    ledger = _challenge_ledger(binding)
    assert challenge_binding(ledger, "app-a", _NOW) == binding

    run = ledger["run"]
    assert isinstance(run, dict)
    for field, value in (("actor_user_id", "human-b"), ("compiled_prompt_hash", "sha256:tampered")):
        tampered = {"run": {**run, field: value}}
        with pytest.raises(ConflictDetected, match="challenge_binding_invalid"):
            challenge_binding(tampered, "app-a", _NOW)


@pytest.mark.parametrize(
    ("ledger", "reason"),
    [
        (None, "challenge_not_found"),
        ({"run": "invalid"}, "challenge_invalid"),
        ({"run": {"status": "failed", "budget_json": {}}}, "challenge_invalid"),
        ({"run": {"status": "started", "budget_json": {"kind": "wrong"}}}, "challenge_invalid"),
    ],
)
def test_challenge_binding_rejects_missing_or_wrong_record_shape(ledger: object, reason: str) -> None:
    with pytest.raises(ConflictDetected, match=reason):
        challenge_binding(ledger, "app-a", _NOW)  # type: ignore[arg-type]


def test_challenge_binding_rejects_expired_application_mismatch_and_malformed_identity() -> None:
    ledger = _challenge_ledger()
    with pytest.raises(ConflictDetected, match="challenge_application_mismatch"):
        challenge_binding(ledger, "app-b", _NOW)

    run = ledger["run"]
    assert isinstance(run, dict)
    budget = run["budget_json"]
    assert isinstance(budget, dict)
    with pytest.raises(ConflictDetected, match="challenge_expired"):
        challenge_binding({"run": {**run, "budget_json": {**budget, "expiresAt": "not-a-time"}}}, "app-a", _NOW)

    payload = budget["requestBinding"]
    assert isinstance(payload, dict)
    missing_tool = {name: value for name, value in payload.items() if name != "toolId"}
    for replacement in ({**payload, "actorUserId": 7}, missing_tool):
        malformed = {"run": {**run, "budget_json": {**budget, "requestBinding": replacement}}}
        with pytest.raises(ConflictDetected, match="challenge_binding_invalid"):
            challenge_binding(malformed, "app-a", _NOW)


def test_challenge_and_receipt_replay_check_the_complete_durable_row_binding() -> None:
    binding = _binding()
    challenge = _challenge_ledger(binding)
    receipt = _receipt_ledger(binding)
    assert challenge_replay_conflict_reason(challenge, binding, _NOW) is None
    assert receipt_conflict_reason(receipt, binding, _NOW) is None

    for ledger, check, expected in (
        (challenge, challenge_replay_conflict_reason, "challenge_binding_mismatch"),
        (receipt, receipt_conflict_reason, "receipt_binding_mismatch"),
    ):
        run = ledger["run"]
        assert isinstance(run, dict)
        tampered = {"run": {**run, "actor_user_id": "human-b"}}
        assert check(tampered, binding, _NOW) == expected

    cross_session = replace(binding, session_id="widget-frame-session")
    assert challenge_replay_conflict_reason(challenge, cross_session, _NOW) is None
    assert receipt_conflict_reason(receipt, cross_session, _NOW) is None


def test_replay_helpers_fail_closed_for_incomplete_evidence() -> None:
    assert terminal_replay([]) is None
    assert terminal_replay([{"result_json": {"status": "ok"}}]) is None
    assert terminal_replay([{"id": "call-a", "result_json": "invalid"}]) is None
    replay = terminal_replay([{"id": "call-a", "result_json": {"status": "ok"}}])
    assert replay is not None and replay.tool_call_id == "call-a" and replay.is_error is False

    assert failed_replay({"id": "run-a", "error_json": {}}) is None
    assert failed_replay({"id": 7, "error_json": {"mcpToolResult": {"status": "failed"}}}) is None
    failed = failed_replay({"id": "run-a", "error_json": {"mcpToolResult": {"status": "failed"}}})
    assert failed is not None and failed.tool_call_id == "run-a-tool-1" and failed.is_error is True


def test_control_principal_time_sequence_and_budget_helpers_preserve_security_defaults() -> None:
    binding = _binding()
    assert execution_binding_budget(binding)["requestBindingHash"] == binding.fingerprint
    assert binding_matches(_challenge_ledger(binding)["run"], binding) is True  # type: ignore[arg-type]
    assert is_expired("not-a-time", _NOW) is True
    assert is_expired(None, _NOW) is True
    assert next_sequence([{"sequence": 4}, {"sequence": "5"}, {}]) == 5

    require_human_control_principal(_context(application_id=None, client_id=None))
    with pytest.raises(PermissionDenied, match="human control-plane principal"):
        require_human_control_principal(_context())
