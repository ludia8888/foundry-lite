from __future__ import annotations

import json
from argparse import Namespace

import pytest

from scripts.operations import issue_macmini_external_oidc_tokens as subject


def test_issue_uses_two_accounts_and_never_writes_raw_values_to_receipt(tmp_path, monkeypatch) -> None:
    state = tmp_path / "state"
    state.mkdir()
    principals = state / "keycloak-qa-principals.txt"
    principals.write_text(
        "author_username=author\nauthor_password=author-secret\n"
        "reviewer_username=reviewer\nreviewer_password=reviewer-secret\n",
        encoding="utf-8",
    )
    principals.chmod(0o600)
    client = subject.IssuedClient("client-1", "https://identity.example/register/client-1", "registration-secret")
    users: list[str] = []
    monkeypatch.setattr(subject, "QA_ROOT", tmp_path)
    monkeypatch.setattr(subject, "assert_host_boundary", lambda: None)
    monkeypatch.setattr(subject, "utc_now", lambda: "2026-08-26T00:00:00Z")
    monkeypatch.setattr(subject, "_json_request", lambda _url: {})
    monkeypatch.setattr(subject, "_register_client", lambda _discovery: client)
    monkeypatch.setattr(
        subject,
        "_authorization_code_token",
        lambda _discovery, _client, _origin, _audience, username, _password: (
            users.append(username) or f"raw-{username}-token"
        ),
    )
    monkeypatch.setattr(
        subject,
        "write_json_receipt",
        lambda path, payload: path.write_text(json.dumps(payload), encoding="utf-8"),
    )

    receipt, issued = subject.issue(
        Namespace(
            run_id="run-1",
            identity_base_url="https://identity.example",
            audience="https://foundry.example/mcp/release/foundry-lite",
            principals_file=str(principals),
        )
    )

    serialized = json.dumps(receipt)
    assert users == ["author", "reviewer"]
    assert issued == client
    assert "author-secret" not in serialized
    assert "raw-author-token" not in serialized
    assert (state / "author-token").stat().st_mode & 0o077 == 0


def test_issue_deletes_registered_client_when_authorization_fails(tmp_path, monkeypatch) -> None:
    state = tmp_path / "state"
    state.mkdir()
    principals = state / "keycloak-qa-principals.txt"
    principals.write_text(
        "author_username=author\nauthor_password=author-secret\n"
        "reviewer_username=reviewer\nreviewer_password=reviewer-secret\n",
        encoding="utf-8",
    )
    principals.chmod(0o600)
    client = subject.IssuedClient("client-1", "https://identity.example/register/client-1", "registration-secret")
    deleted: list[object] = []
    monkeypatch.setattr(subject, "QA_ROOT", tmp_path)
    monkeypatch.setattr(subject, "assert_host_boundary", lambda: None)
    monkeypatch.setattr(subject, "_json_request", lambda _url: {})
    monkeypatch.setattr(subject, "_register_client", lambda _discovery: client)
    monkeypatch.setattr(subject, "_authorization_code_token", lambda *_args: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(subject, "delete_client", lambda value: deleted.append(value) or True)

    with pytest.raises(RuntimeError, match="x"):
        subject.issue(
            Namespace(
                run_id="run-1",
                identity_base_url="https://identity.example",
                audience="https://foundry.example/mcp/release/foundry-lite",
                principals_file=str(principals),
            )
        )

    assert deleted == [client]


def test_form_parser_captures_login_and_consent_controls() -> None:
    raw = b'<form method="post" action="/login"><input name="username"><input name="password"></form>'
    login = subject._form(raw, required_field="password")
    consent = subject._form(
        b'<form method="post" action="/consent">'
        b'<input name="session_code" value="x">'
        b'<button name="accept">Yes</button></form>',
        required_button="accept",
    )

    assert login.action == "/login"
    assert consent.fields == {"session_code": "x"}
    assert consent.buttons == {"accept"}
