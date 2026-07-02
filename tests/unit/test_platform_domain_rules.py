from __future__ import annotations

import pytest
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed
from foundry_lite.domain.platform.evidence import redact_evidence
from foundry_lite.domain.platform.idempotency import StoredIdempotency, decide_idempotency, require_idempotency_key
from foundry_lite.domain.platform.scopes import is_scope_allowed, resource_scope, validate_resource_scope
from foundry_lite.domain.platform.state import (
    ensure_nonterminal_transition,
    is_retryable_terminal_state,
    is_terminal_state,
)
from foundry_lite.domain.platform.traffic import decide_write_traffic


def test_idempotency_decides_new_replay_and_conflict() -> None:
    assert decide_idempotency(None, "sha256:new") == "new"
    assert decide_idempotency(StoredIdempotency("sha256:same", {"ok": True}), "sha256:same") == "replay"

    with pytest.raises(ConflictDetected):
        decide_idempotency(StoredIdempotency("sha256:old", {}), "sha256:new")


def test_idempotency_key_is_required() -> None:
    with pytest.raises(ValidationFailed):
        require_idempotency_key("", surface="unit test mutation")


def test_resource_scope_vocabulary_and_matching() -> None:
    scope = resource_scope("object", "Order", "read")

    validate_resource_scope(scope, "object", "Order")
    assert is_scope_allowed(scope, [scope], [scope])
    assert is_scope_allowed(scope, ["osdk:*"], [scope])
    assert not is_scope_allowed(scope, [scope], [])
    assert not is_scope_allowed(scope, [], [scope])

    with pytest.raises(ValidationFailed):
        resource_scope("object", "Order", "delete")
    with pytest.raises(ValidationFailed):
        validate_resource_scope("osdk:object:Order:execute", "object", "Order")


def test_resource_scope_requires_api_name() -> None:
    with pytest.raises(ValidationFailed):
        resource_scope("object", "", "read")


def test_terminal_state_rules_block_terminal_transitions() -> None:
    assert is_terminal_state("succeeded")
    assert is_retryable_terminal_state("failed")
    assert not is_retryable_terminal_state("succeeded")
    ensure_nonterminal_transition("running", "succeeded", resource_id="run-1")

    with pytest.raises(ConflictDetected):
        ensure_nonterminal_transition("succeeded", "failed", resource_id="run-1")


def test_evidence_redaction_masks_sensitive_keys_but_keeps_token_counts() -> None:
    payload = {
        "accessToken": "raw-token",
        "nested": [{"password": "raw-password"}, {"inputTokens": 12}],
        "authorizationDecision": "allow",
    }

    redacted = redact_evidence(payload, {"customSecret"})

    assert redacted["accessToken"] == "***REDACTED***"
    assert redacted["nested"][0]["password"] == "***REDACTED***"
    assert redacted["nested"][1]["inputTokens"] == 12
    assert redacted["authorizationDecision"] == "allow"


def test_evidence_redaction_matches_normalized_sensitive_keys() -> None:
    redacted = redact_evidence({"api_token": "raw"}, {"apiToken"})

    assert redacted["api_token"] == "***REDACTED***"


def test_evidence_redaction_matches_exact_sensitive_keys() -> None:
    redacted = redact_evidence({"custom": "raw"}, {"custom"})

    assert redacted["custom"] == "***MASKED***"


def test_evidence_redaction_masks_forbidden_nonsecret_keys() -> None:
    redacted = redact_evidence({"prompt": "hello there"})

    assert redacted["prompt"] == "***MASKED***"


def test_evidence_redaction_preserves_already_redacted_values() -> None:
    redacted = redact_evidence({"password": "***REDACTED***"})

    assert redacted["password"] == "***REDACTED***"


def test_evidence_redaction_recurses_into_tuples() -> None:
    redacted = redact_evidence((1, {"password": "raw"}, 2))

    assert redacted == (1, {"password": "***REDACTED***"}, 2)


def test_write_traffic_decision_blocks_active_restore_mode() -> None:
    restore_mode = {
        "restoreId": "restore-1",
        "status": "active",
        "is_write_traffic_paused": True,
        "is_serving_traffic_open": False,
    }

    decide_write_traffic(None, operation="object_edit", resource_type="object", resource_id="Order/O-1")
    with pytest.raises(ConflictDetected):
        decide_write_traffic(
            restore_mode,
            operation="object_edit",
            resource_type="object",
            resource_id="Order/O-1",
        )
