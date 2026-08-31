from __future__ import annotations

import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier

import pytest
from foundry_lite.application.services.aip import governed_release_security as security_module
from foundry_lite.application.services.aip.governed_release_security import GovernedReleaseSecurityLedger
from foundry_lite.application.services.aip.governed_release_security_contract import (
    GovernedReleaseBinding,
    action_run_id,
    preparation_run_id,
    release_binding,
    widget_receipt_id,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories.ai_run_repository import SqlAlchemyAiRunRepository
from foundry_lite.security.policy import PolicyService
from foundry_lite.security.tenant_context import current_tenant_id
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine

_APP_ID = "release-security-app"
_SESSION_ID = "release-security-session"
_ORIGIN = "https://chatgpt.com"
_RELEASE_SCOPE = "osdk:connector:governed_release:execute"

SecurityHarness = tuple[GovernedReleaseSecurityLedger, Engine, SqlAlchemyAiRunRepository]


class _AuditSpy:
    """Capture release-security audit evidence for the isolated ledger fixture."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def _audit(self, _conn, _ctx, **evidence: object) -> None:
        self.events.append(dict(evidence))


@pytest.fixture
def security_harness(tmp_path: Path) -> SecurityHarness:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'governed-release-security.db'}",
        connect_args={"timeout": 30},
        future=True,
    )
    db.create_database(engine)
    repository = SqlAlchemyAiRunRepository(engine)
    ledger = GovernedReleaseSecurityLedger(engine, repository, PolicyService(), _AuditSpy())
    return ledger, engine, repository


def test_release_security_binds_authenticated_tenant_before_ledger_transactions(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'tenant-context.db'}", future=True)
    db.create_database(engine)
    observed_tenants: list[str | None] = []
    event.listen(engine, "begin", lambda _conn: observed_tenants.append(current_tenant_id()))
    repository = SqlAlchemyAiRunRepository(engine)
    ledger = GovernedReleaseSecurityLedger(engine, repository, PolicyService(), _AuditSpy())
    ctx = _context()
    binding = _binding(ctx)

    prepared = ledger.prepare(ctx, binding)
    ledger.replay(ctx, action_run_id(binding), binding)

    assert isinstance(prepared["widgetConfirmationToken"], str)
    assert observed_tenants == [ctx.tenant_id, ctx.tenant_id]
    assert current_tenant_id() is None


def test_confirmation_expires_before_it_can_claim_an_action(
    security_harness: SecurityHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, engine, repository = security_harness
    clock = {"now": "2026-08-09T00:00:00+00:00"}
    monkeypatch.setattr(security_module, "_now", lambda: clock["now"])
    ctx = _context()
    binding = _binding(ctx)
    token = _prepare(ledger, ctx, binding)
    clock["now"] = "2026-08-09T00:05:01+00:00"

    with pytest.raises(ConflictDetected) as raised:
        ledger.claim(ctx, action_run_id(binding), binding, token)

    assert raised.value.details == {"reason": "widget_confirmation_expired"}
    assert _run_status(_load_ledger(engine, repository, ctx.tenant_id, widget_receipt_id(token))) == "running"


def _arguments(*, proposal_id: str = "pipeline-proposal-1") -> dict[str, object]:
    return {
        "releaseKind": "pipeline",
        "proposalId": proposal_id,
        "pipelineId": "pipeline-1",
        "versionId": "version-2",
        "idempotencyKey": "release-security-idempotency-key",
    }


@pytest.mark.parametrize(
    ("context_changes", "binding_changes"),
    [
        ({"actor_user_id": "release-security-other-user"}, {"session_id": "other-actor-session"}),
        ({"application_id": "release-security-other-app"}, {"session_id": "other-app-session"}),
        ({"client_id": "release-security-other-client"}, {}),
        ({"oauth_session_hash": "oauth-session:sha256:other-session"}, {}),
        ({"oauth_session_authority": "issuer"}, {}),
        ({"authorization_server_issuer": "https://other-issuer.example.test"}, {}),
        ({"oauth_resource": "https://foundry.example.test/mcp/release/other-app"}, {}),
        ({}, {"origin": "https://evil.example"}),
        ({}, {"arguments": _arguments(proposal_id="different-proposal")}),
    ],
    ids=(
        "actor",
        "application",
        "client",
        "oauth-session-hash",
        "oauth-session-authority",
        "issuer",
        "resource",
        "origin",
        "arguments",
    ),
)
def test_confirmation_is_bound_to_every_security_and_request_dimension(
    security_harness: SecurityHarness,
    context_changes: dict[str, object],
    binding_changes: dict[str, object],
) -> None:
    ledger, engine, repository = security_harness
    original_ctx = _context()
    original_binding = _binding(original_ctx)
    token = _prepare(ledger, original_ctx, original_binding)
    attempted_ctx = replace(original_ctx, **context_changes)
    attempted_binding = _binding(attempted_ctx, **binding_changes)

    with pytest.raises(ConflictDetected) as raised:
        ledger.claim(
            attempted_ctx,
            action_run_id(attempted_binding),
            attempted_binding,
            token,
        )

    assert raised.value.details == {"reason": "widget_confirmation_binding_mismatch"}
    receipt = _load_ledger(engine, repository, original_ctx.tenant_id, widget_receipt_id(token))
    assert _run_status(receipt) == "running"


def test_confirmation_survives_host_transport_session_rotation(
    security_harness: SecurityHarness,
) -> None:
    """MCP Apps may prepare and execute an exact action on different transport sessions."""

    ledger, engine, repository = security_harness
    ctx = _context()
    prepared_binding = _binding(ctx, session_id="release-security-prepare-session")
    token = _prepare(ledger, ctx, prepared_binding)
    action_binding = _binding(ctx, session_id="release-security-action-session")

    assert preparation_run_id(action_binding) == preparation_run_id(prepared_binding)
    assert action_run_id(action_binding) == action_run_id(prepared_binding)
    assert action_binding.fingerprint == prepared_binding.fingerprint
    assert ledger.claim(ctx, action_run_id(action_binding), action_binding, token) is True

    action = _load_ledger(engine, repository, ctx.tenant_id, action_run_id(action_binding))
    receipt = _load_ledger(engine, repository, ctx.tenant_id, widget_receipt_id(token))
    assert action is not None
    assert action["run"]["session_id"] == "release-security-action-session"
    assert action["run"]["budget_json"]["requestBinding"]["sessionId"] == ("release-security-action-session")
    assert _run_status(receipt) == "succeeded"


def test_confirmation_survives_access_token_rotation_in_the_same_oauth_session(
    security_harness: SecurityHarness,
) -> None:
    """A short-lived access token may refresh between the prepare and action calls."""

    ledger, engine, repository = security_harness
    prepared_ctx = _context()
    prepared_binding = _binding(prepared_ctx)
    token = _prepare(ledger, prepared_ctx, prepared_binding)
    action_ctx = replace(
        prepared_ctx,
        oauth_token_issued_at=1_786_224_060,
        oauth_token_expires_at=1_786_224_180,
    )
    action_binding = _binding(action_ctx)

    assert action_binding.fingerprint == prepared_binding.fingerprint
    assert action_run_id(action_binding) == action_run_id(prepared_binding)
    assert (
        ledger.claim(
            action_ctx,
            action_run_id(action_binding),
            action_binding,
            token,
        )
        is True
    )

    action = _load_ledger(engine, repository, action_ctx.tenant_id, action_run_id(action_binding))
    assert action is not None
    request_binding = action["run"]["budget_json"]["requestBinding"]
    assert request_binding["oauthTokenIssuedAt"] == 1_786_224_060
    assert request_binding["oauthTokenExpiresAt"] == 1_786_224_180


def test_consumed_confirmation_cannot_authorize_a_second_action_attempt(
    security_harness: SecurityHarness,
) -> None:
    ledger, engine, repository = security_harness
    ctx = _context()
    binding = _binding(ctx)
    token = _prepare(ledger, ctx, binding)
    first_run_id = action_run_id(binding)
    second_run_id = f"{first_run_id}-second-attempt"

    assert ledger.claim(ctx, first_run_id, binding, token) is True
    with pytest.raises(ConflictDetected) as raised:
        ledger.claim(ctx, second_run_id, binding, token)

    assert raised.value.details == {"reason": "widget_confirmation_already_consumed"}
    assert _load_ledger(engine, repository, ctx.tenant_id, second_run_id) is None
    receipt = _load_ledger(engine, repository, ctx.tenant_id, widget_receipt_id(token))
    assert _run_status(receipt) == "succeeded"
    assert _event_types(receipt).count("governed_release_widget_confirmation_consumed") == 1


def test_concurrent_confirmation_consumption_has_exactly_one_winner(
    security_harness: SecurityHarness,
) -> None:
    ledger, engine, repository = security_harness
    ctx = _context()
    binding = _binding(ctx)
    token = _prepare(ledger, ctx, binding)
    barrier = Barrier(2, timeout=5)

    def claim(run_id: str) -> bool | str:
        barrier.wait()
        try:
            return ledger.claim(ctx, run_id, binding, token)
        except ConflictDetected as exc:
            return str(exc.details.get("reason"))

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(claim, ("concurrent-release-a", "concurrent-release-b")))

    assert outcomes.count(True) == 1
    assert outcomes.count("widget_confirmation_already_consumed") == 1
    receipt = _load_ledger(engine, repository, ctx.tenant_id, widget_receipt_id(token))
    assert _run_status(receipt) == "succeeded"
    assert _event_types(receipt).count("governed_release_widget_confirmation_consumed") == 1


def test_raw_confirmation_token_is_absent_from_every_persisted_database_row(
    security_harness: SecurityHarness,
) -> None:
    ledger, engine, _ = security_harness
    ctx = _context()
    binding = _binding(ctx)

    token = _prepare(ledger, ctx, binding)

    with engine.begin() as transaction:
        persisted = {
            table.name: [dict(row) for row in transaction.execute(select(table)).mappings().all()]
            for table in db.metadata.sorted_tables
        }
    serialized = json.dumps(persisted, sort_keys=True, default=str)
    assert token not in serialized
    assert str(ctx.oauth_session_id) not in serialized
    assert str(ctx.oauth_session_hash) in serialized
    assert '"oauthGrantType": "authorization_code"' in serialized
    assert '"requiredScope": "osdk:connector:governed_release:execute"' in serialized
    assert widget_receipt_id(token) in serialized


def test_exact_prepare_replay_rotates_the_lost_token_and_revokes_the_old_hash_receipt(
    security_harness: SecurityHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, engine, repository = security_harness
    clock = {"now": "2026-08-09T00:00:00+00:00"}
    monkeypatch.setattr(security_module, "_now", lambda: clock["now"])
    ctx = _context()
    binding = _binding(ctx)
    first = ledger.prepare(ctx, binding)
    first_token = str(first["widgetConfirmationToken"])
    clock["now"] = "2026-08-09T00:00:01+00:00"

    recovered = ledger.prepare(ctx, binding)
    active_token = str(recovered["widgetConfirmationToken"])

    assert recovered["isReplayed"] is True
    assert active_token != first_token
    preparation = _load_ledger(engine, repository, ctx.tenant_id, preparation_run_id(binding))
    assert preparation is not None
    assert preparation["run"]["budget_json"]["receiptId"] == widget_receipt_id(active_token)
    assert _run_status(_load_ledger(engine, repository, ctx.tenant_id, widget_receipt_id(first_token))) == "succeeded"
    assert _run_status(_load_ledger(engine, repository, ctx.tenant_id, widget_receipt_id(active_token))) == "running"
    with pytest.raises(ConflictDetected) as revoked:
        ledger.claim(ctx, action_run_id(binding), binding, first_token)
    assert revoked.value.details == {"reason": "widget_confirmation_already_consumed"}
    assert ledger.claim(ctx, action_run_id(binding), binding, active_token) is True

    with engine.begin() as transaction:
        persisted = {
            table.name: [dict(row) for row in transaction.execute(select(table)).mappings().all()]
            for table in db.metadata.sorted_tables
        }
    serialized = json.dumps(persisted, sort_keys=True, default=str)
    assert first_token not in serialized
    assert active_token not in serialized


def test_concurrent_exact_prepare_rotations_leave_only_one_usable_token(
    security_harness: SecurityHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, engine, repository = security_harness
    clock = {"now": "2026-08-09T00:00:00+00:00"}
    monkeypatch.setattr(security_module, "_now", lambda: clock["now"])
    ctx = _context()
    binding = _binding(ctx)
    original = ledger.prepare(ctx, binding)
    original_token = str(original["widgetConfirmationToken"])
    clock["now"] = "2026-08-09T00:00:01+00:00"
    barrier = Barrier(2, timeout=5)

    def rotate() -> dict[str, object] | str:
        barrier.wait()
        try:
            return ledger.prepare(ctx, binding)
        except ConflictDetected as exc:
            return str(exc.details.get("reason"))

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: rotate(), range(2)))

    successful = [row for row in outcomes if isinstance(row, dict)]
    assert successful
    tokens = [original_token, *(str(row["widgetConfirmationToken"]) for row in successful)]
    preparation = _load_ledger(engine, repository, ctx.tenant_id, preparation_run_id(binding))
    assert preparation is not None
    active_receipt_id = preparation["run"]["budget_json"]["receiptId"]
    active_tokens = [token for token in tokens if widget_receipt_id(token) == active_receipt_id]
    assert len(active_tokens) == 1
    for token in (token for token in tokens if token != active_tokens[0]):
        with pytest.raises(ConflictDetected):
            ledger.claim(ctx, action_run_id(binding), binding, token)
    assert ledger.claim(ctx, action_run_id(binding), binding, active_tokens[0]) is True


def test_stale_action_run_recovers_without_a_second_confirmation_and_replays_exact_result(
    security_harness: SecurityHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, engine, repository = security_harness
    clock = {"now": "2026-08-09T00:00:00+00:00"}
    monkeypatch.setattr(security_module, "_now", lambda: clock["now"])
    ctx = _context()
    binding = _binding(ctx)
    run_id = action_run_id(binding)
    token = _prepare(ledger, ctx, binding)

    assert ledger.claim(ctx, run_id, binding, token) is True
    with pytest.raises(ConflictDetected) as in_progress:
        ledger.recover(ctx, run_id, binding)
    assert in_progress.value.details["reason"] == "release_run_in_progress"
    assert in_progress.value.details["isRecoverable"] is True

    clock["now"] = "2026-08-09T00:00:31+00:00"
    execution_attempt = ledger.recover(ctx, run_id, binding)
    assert execution_attempt == 1
    output = {"release": {"proposalId": "pipeline-proposal-1", "stage": "deployed"}}
    tool_call_id = ledger.complete(ctx, run_id, binding, output, execution_attempt)
    assert ledger.complete(ctx, run_id, binding, output, execution_attempt) == tool_call_id

    replay = ledger.replay(ctx, run_id, binding)
    assert replay is not None
    assert replay.tool_call_id == tool_call_id
    assert replay.output == output
    action_ledger = _load_ledger(engine, repository, ctx.tenant_id, run_id)
    assert _run_status(action_ledger) == "succeeded"
    assert len(action_ledger["toolCalls"]) == 1
    assert _event_types(action_ledger) == [
        "governed_release_tool_running",
        "governed_release_recovery_claimed",
        "succeeded",
    ]


def test_stale_action_recovery_still_requires_the_exact_original_binding(
    security_harness: SecurityHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, _, _ = security_harness
    clock = {"now": "2026-08-09T00:00:00+00:00"}
    monkeypatch.setattr(security_module, "_now", lambda: clock["now"])
    ctx = _context()
    binding = _binding(ctx)
    run_id = action_run_id(binding)
    token = _prepare(ledger, ctx, binding)
    assert ledger.claim(ctx, run_id, binding, token) is True
    changed = _binding(ctx, arguments=_arguments(proposal_id="different-proposal"))
    assert action_run_id(changed) == run_id
    clock["now"] = "2026-08-09T00:00:31+00:00"

    with pytest.raises(ConflictDetected) as raised:
        ledger.recover(ctx, run_id, changed)

    assert raised.value.details == {"reason": "widget_confirmation_binding_mismatch"}


def test_only_one_worker_can_claim_each_stale_action_recovery_lease(
    security_harness: SecurityHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, engine, repository = security_harness
    clock = {"now": "2026-08-09T00:00:00+00:00"}
    monkeypatch.setattr(security_module, "_now", lambda: clock["now"])
    ctx = _context()
    binding = _binding(ctx)
    run_id = action_run_id(binding)
    token = _prepare(ledger, ctx, binding)
    assert ledger.claim(ctx, run_id, binding, token) is True
    clock["now"] = "2026-08-09T00:00:31+00:00"
    barrier = Barrier(2, timeout=5)

    def recover() -> int | str | None:
        barrier.wait()
        try:
            return ledger.recover(ctx, run_id, binding)
        except ConflictDetected as exc:
            return str(exc.details.get("reason"))

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: recover(), range(2)))

    assert outcomes.count(1) == 1
    assert outcomes.count(None) + outcomes.count("release_run_in_progress") == 1
    action_ledger = _load_ledger(engine, repository, ctx.tenant_id, run_id)
    assert _event_types(action_ledger).count("governed_release_recovery_claimed") == 1


def test_recovery_lease_fences_the_original_worker_late_failure_and_completion(
    security_harness: SecurityHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger, engine, repository = security_harness
    clock = {"now": "2026-08-09T00:00:00+00:00"}
    monkeypatch.setattr(security_module, "_now", lambda: clock["now"])
    ctx = _context()
    binding = _binding(ctx)
    run_id = action_run_id(binding)
    token = _prepare(ledger, ctx, binding)
    assert ledger.claim(ctx, run_id, binding, token) is True
    clock["now"] = "2026-08-09T00:00:31+00:00"
    recovery_attempt = ledger.recover(ctx, run_id, binding)
    assert recovery_attempt == 1

    ledger.fail(ctx, run_id, binding, RuntimeError("late original failure"), execution_attempt=0)
    assert _run_status(_load_ledger(engine, repository, ctx.tenant_id, run_id)) == "running"
    output = {"release": {"proposalId": "pipeline-proposal-1", "stage": "deployed"}}
    ledger.complete(ctx, run_id, binding, output, recovery_attempt)
    ledger.fail(ctx, run_id, binding, RuntimeError("later original failure"), execution_attempt=0)
    with pytest.raises(ConflictDetected) as stale_complete:
        ledger.complete(ctx, run_id, binding, output, execution_attempt=0)

    assert stale_complete.value.details == {"reason": "release_execution_lease_lost"}
    action_ledger = _load_ledger(engine, repository, ctx.tenant_id, run_id)
    assert _run_status(action_ledger) == "succeeded"
    assert "failed" not in _event_types(action_ledger)
    assert len(action_ledger["toolCalls"]) == 1


def test_known_not_committed_failure_requires_fresh_confirmation_before_retry(
    security_harness: SecurityHarness,
) -> None:
    ledger, engine, repository = security_harness
    ctx = _context()
    binding = _binding(ctx)
    run_id = action_run_id(binding)
    consumed_token = _prepare(ledger, ctx, binding)
    assert ledger.claim(ctx, run_id, binding, consumed_token) is True
    ledger.fail(
        ctx,
        run_id,
        binding,
        ValidationFailed("proposal was stale before mutation"),
        is_known_not_committed=True,
    )

    failed = _load_ledger(engine, repository, ctx.tenant_id, run_id)
    assert _run_status(failed) == "failed"
    assert failed is not None
    assert failed["run"]["error_json"]["knownNotCommitted"] is True
    assert failed["run"]["error_json"]["safeToRetry"] is True
    assert ledger.is_fresh_failed_retry(ctx, run_id, binding, consumed_token) is False
    with pytest.raises(ConflictDetected) as stale:
        ledger.retry_failed(ctx, run_id, binding, consumed_token)
    assert stale.value.details == {"reason": "widget_confirmation_already_consumed"}

    fresh_token = _prepare(ledger, ctx, binding)
    assert ledger.is_fresh_failed_retry(ctx, run_id, binding, fresh_token) is True
    attempt = ledger.retry_failed(ctx, run_id, binding, fresh_token)
    assert attempt == 1
    output = {"release": {"proposalId": "pipeline-proposal-1", "stage": "deployed"}}
    ledger.complete(ctx, run_id, binding, output, attempt)

    completed = _load_ledger(engine, repository, ctx.tenant_id, run_id)
    assert _run_status(completed) == "succeeded"
    assert _event_types(completed) == [
        "governed_release_tool_running",
        "failed",
        "governed_release_failed_run_reopened",
        "succeeded",
    ]


def test_proposal_scoped_action_audit_is_fenced_safe_and_not_duplicated_on_replay(
    security_harness: SecurityHarness,
) -> None:
    ledger, _, _ = security_harness
    ctx = _context()
    binding = _binding(ctx)
    run_id = action_run_id(binding)
    consumed_token = _prepare(ledger, ctx, binding)
    assert ledger.claim(ctx, run_id, binding, consumed_token) is True
    ledger.fail(
        ctx,
        run_id,
        binding,
        ValidationFailed("private pre-mutation detail"),
        is_known_not_committed=True,
    )
    fresh_token = _prepare(ledger, ctx, binding)
    attempt = ledger.retry_failed(ctx, run_id, binding, fresh_token)
    assert attempt == 1
    output = {"release": {"proposalId": binding.proposal_id, "stage": "deployed"}}
    tool_call_id = ledger.complete(ctx, run_id, binding, output, attempt)
    assert ledger.complete(ctx, run_id, binding, output, attempt) == tool_call_id

    audit = ledger.audit
    assert isinstance(audit, _AuditSpy)
    events = [
        event for event in audit.events if str(event.get("event_type", "")).startswith("governed_release.action.")
    ]
    assert [event["event_type"] for event in events] == [
        "governed_release.action.started",
        "governed_release.action.failed",
        "governed_release.action.retry_started",
        "governed_release.action.succeeded",
    ]
    assert all(event["resource_type"] == "governed_release_proposal" for event in events)
    assert all(event["resource_id"] == binding.proposal_id for event in events)
    assert all(event["correlation_id"] == run_id for event in events)
    serialized = json.dumps(events, sort_keys=True, default=str)
    assert consumed_token not in serialized
    assert fresh_token not in serialized
    assert "private pre-mutation detail" not in serialized


def test_unclassified_terminal_failure_cannot_receive_a_fresh_retry_receipt(
    security_harness: SecurityHarness,
) -> None:
    ledger, engine, repository = security_harness
    ctx = _context()
    binding = _binding(ctx)
    run_id = action_run_id(binding)
    first_token = _prepare(ledger, ctx, binding)
    assert ledger.claim(ctx, run_id, binding, first_token) is True
    ledger.fail(ctx, run_id, binding, RuntimeError("unclassified failure"))

    with pytest.raises(ConflictDetected) as unsafe:
        ledger.prepare(ctx, binding)

    assert unsafe.value.details == {"reason": "release_run_not_safely_retryable"}
    assert _run_status(_load_ledger(engine, repository, ctx.tenant_id, run_id)) == "failed"


@pytest.mark.parametrize("run_state", ["running", "outcome_unknown", "succeeded"])
def test_running_unknown_or_succeeded_action_cannot_receive_failed_retry_receipt(
    security_harness: SecurityHarness,
    run_state: str,
) -> None:
    ledger, _, _ = security_harness
    ctx = _context()
    binding = _binding(ctx)
    run_id = action_run_id(binding)
    first_token = _prepare(ledger, ctx, binding)
    assert ledger.claim(ctx, run_id, binding, first_token) is True
    if run_state == "outcome_unknown":
        ledger.defer(ctx, run_id, binding, RuntimeError("commit outcome unknown"))
    if run_state == "succeeded":
        ledger.complete(ctx, run_id, binding, {"release": {"stage": "deployed"}})

    with pytest.raises(ConflictDetected) as refused:
        ledger.prepare(ctx, binding)

    assert refused.value.details == {"reason": "release_run_not_safely_retryable"}


def test_concurrent_safe_failed_retry_has_one_fenced_winner(
    security_harness: SecurityHarness,
) -> None:
    ledger, engine, repository = security_harness
    ctx = _context()
    binding = _binding(ctx)
    run_id = action_run_id(binding)
    first_token = _prepare(ledger, ctx, binding)
    assert ledger.claim(ctx, run_id, binding, first_token) is True
    ledger.fail(
        ctx,
        run_id,
        binding,
        ValidationFailed("known pre-mutation failure"),
        is_known_not_committed=True,
    )
    fresh_token = _prepare(ledger, ctx, binding)
    barrier = Barrier(2, timeout=5)

    def retry() -> int | str | None:
        barrier.wait()
        try:
            return ledger.retry_failed(ctx, run_id, binding, fresh_token)
        except ConflictDetected as exc:
            return str(exc.details.get("reason"))

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: retry(), range(2)))

    assert outcomes.count(1) == 1
    assert outcomes.count(None) + outcomes.count("widget_confirmation_already_consumed") == 1
    action_ledger = _load_ledger(engine, repository, ctx.tenant_id, run_id)
    assert _run_status(action_ledger) == "running"
    assert _event_types(action_ledger).count("governed_release_failed_run_reopened") == 1


def _context() -> RequestContext:
    return RequestContext(
        tenant_id="tenant-release-security",
        actor_user_id="release-security-user",
        roles=("admin",),
        application_id=_APP_ID,
        client_id="release-security-client",
        token_scopes=(_RELEASE_SCOPE,),
        oauth_session_id="release-security-oauth-session",
        oauth_session_hash="oauth-session:sha256:release-security-session",
        oauth_session_authority="local",
        authorization_server_issuer="https://foundry-lite.local/osdk-oauth",
        oauth_grant_type="authorization_code",
        oauth_resource=f"https://foundry.example.test/mcp/release/{_APP_ID}",
        oauth_token_issued_at=1_786_224_000,
        oauth_token_expires_at=1_786_224_900,
        is_human_oauth=True,
    )


def _binding(
    ctx: RequestContext,
    *,
    session_id: str = _SESSION_ID,
    origin: str = _ORIGIN,
    arguments: Mapping[str, object] | None = None,
) -> GovernedReleaseBinding:
    assert isinstance(ctx.application_id, str)
    return release_binding(
        ctx,
        application_id=ctx.application_id,
        session_id=session_id,
        tool_name="deploy_release",
        arguments=arguments or _arguments(),
        required_permission="pipeline:deploy",
        origin=origin,
    )


def _prepare(
    ledger: GovernedReleaseSecurityLedger,
    ctx: RequestContext,
    binding: GovernedReleaseBinding,
) -> str:
    prepared = ledger.prepare(ctx, binding)
    token = prepared.get("widgetConfirmationToken")
    assert isinstance(token, str)
    return token


def _load_ledger(
    engine: Engine,
    repository: SqlAlchemyAiRunRepository,
    tenant_id: str,
    run_id: str,
) -> Mapping[str, object] | None:
    with engine.begin() as transaction:
        return repository.ledger_for_run(
            transaction=transaction,
            tenant_id=tenant_id,
            ai_run_id=run_id,
        )


def _run_status(ledger: Mapping[str, object] | None) -> object:
    assert ledger is not None
    run = ledger.get("run")
    assert isinstance(run, Mapping)
    return run.get("status")


def _event_types(ledger: Mapping[str, object] | None) -> list[object]:
    assert ledger is not None
    events = ledger.get("events")
    assert isinstance(events, list)
    return [event.get("event_type") for event in events if isinstance(event, Mapping)]
