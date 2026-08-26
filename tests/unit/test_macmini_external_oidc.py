from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.operations import verify_macmini_external_oidc as subject


def _principal(actor: str, session: str, *, client_id: str = "chatgpt-client") -> SimpleNamespace:
    return SimpleNamespace(
        tenant_id="tenant-qa",
        actor_user_id=actor,
        token_scopes=(subject.GOVERNED_RELEASE_SCOPE,),
        client_id=client_id,
        oauth_session_hash=session,
        oauth_session_authority="issuer",
        authorization_server_issuer="https://identity.example.test/realms/foundry-lite",
        oauth_grant_type="authorization_code",
        oauth_resource="https://foundry.example.test/mcp/release/foundry-lite",
        is_human_oauth=True,
    )


class _Provider:
    def __init__(self, principals: list[SimpleNamespace]) -> None:
        self._principals = iter(principals)

    def authenticate_for_audience(self, credentials, audience):
        assert credentials["authorization"].startswith("Bearer ")
        assert audience == "https://foundry.example.test/mcp/release/foundry-lite"
        return next(self._principals)


def _args(state: Path) -> Namespace:
    return Namespace(
        run_id="run-1",
        issuer="https://identity.example.test/realms/foundry-lite",
        discovery_url="https://identity.example.test/realms/foundry-lite/.well-known/openid-configuration",
        audience="https://foundry.example.test/mcp/release/foundry-lite",
        allowed_client_id=["chatgpt-client"],
        author_token_file=str(state / "author-token"),
        reviewer_token_file=str(state / "reviewer-token"),
    )


def _token_files(state: Path) -> None:
    state.mkdir(parents=True)
    for name in ("author-token", "reviewer-token"):
        path = state / name
        path.write_text(f"token-{name}", encoding="utf-8")
        path.chmod(0o600)


def test_verify_uses_production_adapter_and_writes_only_hashed_principal_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = tmp_path / "state"
    _token_files(state)
    principals = [
        _principal("author-sub", "oauth-session:sha256:" + "a" * 64),
        _principal("reviewer-sub", "oauth-session:sha256:" + "b" * 64),
    ]
    captured_environment: dict[str, str] = {}
    monkeypatch.setattr(subject, "QA_ROOT", tmp_path)
    monkeypatch.setattr(subject, "assert_host_boundary", lambda: None)
    monkeypatch.setattr(subject, "utc_now", lambda: "2026-08-18T00:00:00Z")
    monkeypatch.setattr(
        subject,
        "write_json_receipt",
        lambda path, payload: path.write_text(json.dumps(payload), encoding="utf-8"),
    )
    monkeypatch.setattr(
        subject,
        "jwt_oidc_auth_provider_from_env",
        lambda environment: captured_environment.update(environment) or _Provider(principals),
    )

    receipt = subject.verify(_args(state))

    serialized = json.dumps(receipt)
    assert receipt["status"] == "passed"
    assert receipt["subjectsDistinct"] is True
    assert receipt["oauthSessionsDistinct"] is True
    assert "author-sub" not in serialized
    assert "reviewer-sub" not in serialized
    assert "token-author" not in serialized
    assert captured_environment[subject.OIDC_CLIENT_ID_CLAIM_ENV] == "azp"
    assert captured_environment[subject.OIDC_SESSION_CLAIM_ENV] == "sid"
    evidence = tmp_path / "evidence/run-1/external-oidc-principals.json"
    assert json.loads(evidence.read_text(encoding="utf-8")) == receipt


def test_verify_rejects_same_verified_subject(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state"
    _token_files(state)
    principals = [
        _principal("same-sub", "oauth-session:sha256:" + "a" * 64),
        _principal("same-sub", "oauth-session:sha256:" + "b" * 64),
    ]
    monkeypatch.setattr(subject, "QA_ROOT", tmp_path)
    monkeypatch.setattr(subject, "assert_host_boundary", lambda: None)
    monkeypatch.setattr(subject, "jwt_oidc_auth_provider_from_env", lambda _environment: _Provider(principals))

    with pytest.raises(RuntimeError, match="subjects_overlap"):
        subject.verify(_args(state))


def test_verify_rejects_different_registered_clients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state"
    _token_files(state)
    principals = [
        _principal("author-sub", "oauth-session:sha256:" + "a" * 64),
        _principal("reviewer-sub", "oauth-session:sha256:" + "b" * 64, client_id="other-client"),
    ]
    args = _args(state)
    args.allowed_client_id.append("other-client")
    monkeypatch.setattr(subject, "QA_ROOT", tmp_path)
    monkeypatch.setattr(subject, "assert_host_boundary", lambda: None)
    monkeypatch.setattr(subject, "jwt_oidc_auth_provider_from_env", lambda _environment: _Provider(principals))

    with pytest.raises(RuntimeError, match="clients_mismatch"):
        subject.verify(args)


def test_verify_rejects_token_file_outside_private_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outside = tmp_path / "outside-token"
    outside.write_text("token", encoding="utf-8")
    outside.chmod(0o600)
    state = tmp_path / "state"
    _token_files(state)
    args = _args(state)
    args.author_token_file = str(outside)
    monkeypatch.setattr(subject, "QA_ROOT", tmp_path)
    monkeypatch.setattr(subject, "assert_host_boundary", lambda: None)

    with pytest.raises(ValueError, match="token_file_invalid"):
        subject.verify(args)
