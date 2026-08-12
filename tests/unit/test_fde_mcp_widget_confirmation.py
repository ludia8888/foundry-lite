from __future__ import annotations

import json
from dataclasses import asdict, replace

from foundry_lite.application.services.aip.fde_mcp_confirmation_contract import FdeMcpRequestBinding
from foundry_lite.application.services.aip.fde_mcp_widget_confirmation import (
    widget_approval_binding,
    widget_approval_payload,
    widget_token_conflict_reason,
    widget_token_id,
    widget_token_record,
    widget_token_recovery_conflict_reason,
)
from foundry_lite.domain.context import RequestContext

_NOW = "2026-08-09T00:00:00+00:00"
_EXPIRES = "2026-08-09T00:05:00+00:00"


def _context() -> RequestContext:
    return RequestContext(
        tenant_id="tenant-a",
        actor_user_id="human-a",
        roles=("data_engineer",),
        application_id="app-a",
        client_id="client-a",
        token_scopes=("osdk:connector:fde_ontology_editing:execute",),
        oauth_session_id="oauth-a",
        request_id="req-a",
    )


def _request_binding() -> FdeMcpRequestBinding:
    return FdeMcpRequestBinding(
        tenant_id="tenant-a",
        actor_user_id="human-a",
        application_id="app-a",
        client_id="client-a",
        oauth_session_id="oauth-a",
        session_id="mcp-builder-a",
        tool_id="ontology.branch.apply_patch",
        mode="ontology_editing",
        workspace_ref="ontology-branch:branch-a",
        arguments_hash="sha256:arguments-a",
        required_permission="ontology:write",
        origin="http://localhost:4173",
    )


def test_widget_token_record_is_hash_only_and_bound_to_every_app_identity_fact() -> None:
    ctx = _context()
    request = _request_binding()
    binding = widget_approval_binding(ctx, request, "challenge-a", request.origin)
    secret = "raw-widget-token-that-must-not-be-stored"
    record = widget_token_record(ctx, binding, widget_token_id(secret), _NOW, _EXPIRES)
    ledger = {"run": asdict(record)}

    assert secret not in json.dumps(asdict(record), sort_keys=True)
    assert widget_token_conflict_reason(ledger, binding, _NOW) is None

    mismatches = (
        replace(binding, tenant_id="tenant-b"),
        replace(binding, actor_user_id="human-b"),
        replace(binding, application_id="app-b"),
        replace(binding, client_id="client-b"),
        replace(binding, oauth_session_id="oauth-b"),
        replace(binding, mcp_session_id="mcp-builder-b"),
        replace(binding, challenge_id="challenge-b"),
        replace(binding, request_binding_hash="sha256:arguments-b"),
        replace(binding, origin="http://127.0.0.1:4173"),
    )
    assert {widget_token_conflict_reason(ledger, mismatch, _NOW) for mismatch in mismatches} == {
        "widget_token_binding_mismatch"
    }


def test_widget_token_rejects_missing_reused_and_expired_ledger() -> None:
    ctx = _context()
    request = _request_binding()
    binding = widget_approval_binding(ctx, request, "challenge-a", request.origin)
    record = widget_token_record(ctx, binding, widget_token_id("secret"), _NOW, _EXPIRES)
    run = asdict(record)

    assert widget_token_conflict_reason(None, binding, _NOW) == "widget_token_not_found"
    assert widget_token_conflict_reason({"run": {**run, "status": "succeeded"}}, binding, _NOW) == (
        "widget_token_already_consumed"
    )
    expired_budget = {**run["budget_json"], "expiresAt": "2026-08-08T23:59:00+00:00"}
    assert widget_token_conflict_reason({"run": {**run, "budget_json": expired_budget}}, binding, _NOW) == (
        "widget_token_expired"
    )
    consumed = {
        "run": {
            **run,
            "status": "succeeded",
            "usage_json": {"source": "builder_mcp_widget_token_consumed"},
        }
    }
    revoked = {
        "run": {
            **run,
            "status": "succeeded",
            "usage_json": {"source": "builder_mcp_widget_token_revoked"},
        }
    }
    assert widget_token_recovery_conflict_reason(consumed, binding) is None
    assert widget_token_recovery_conflict_reason(revoked, binding) == "widget_token_not_consumed"


def test_widget_approval_receipt_is_delivered_only_in_private_meta() -> None:
    result = widget_approval_payload("challenge-a", "receipt-secret", _EXPIRES)

    assert result["_meta"] == {"confirmationReceipt": "receipt-secret"}
    assert "receipt-secret" not in json.dumps(result["structuredContent"], sort_keys=True)
    assert "receipt-secret" not in json.dumps(result["content"], sort_keys=True)
