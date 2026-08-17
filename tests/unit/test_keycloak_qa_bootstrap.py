from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from scripts.operations import bootstrap_keycloak_qa_user as subject


def _environment() -> dict[str, str]:
    return {
        "KEYCLOAK_ADMIN": "qa-admin",
        "KEYCLOAK_ADMIN_PASSWORD": "admin-secret",
        "KEYCLOAK_QA_USER": "sean1234",
        "KEYCLOAK_QA_USER_PASSWORD": "user-secret",
    }


def _role(role: str) -> bytes:
    return json.dumps({"id": f"id-{role}", "name": role}).encode()


def test_bootstrap_updates_existing_user_without_returning_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    responses: Iterator[tuple[int, bytes, str | None]] = iter(
        [
            (200, b'{"access_token":"raw-token"}', None),
            (200, b'[{"id":"user-1","username":"sean1234"}]', None),
            (204, b"", None),
            *((200, _role(role), None) for role in subject._ROLES),
            (204, b"", None),
        ]
    )
    monkeypatch.setattr(subject, "_request", lambda *_args, **_kwargs: next(responses))

    receipt = subject.bootstrap("http://foundry-lite-keycloak:8080", _environment())

    serialized = json.dumps(receipt)
    assert receipt["status"] == "updated"
    assert "admin-secret" not in serialized
    assert "user-secret" not in serialized
    assert "raw-token" not in serialized


def test_bootstrap_creates_missing_user_then_resolves_exact_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    responses: Iterator[tuple[int, bytes, str | None]] = iter(
        [
            (200, b'{"access_token":"raw-token"}', None),
            (200, b"[]", None),
            (201, b"", None),
            (200, b'[{"id":"user-2","username":"sean1234"}]', None),
            (204, b"", None),
            *((200, _role(role), None) for role in subject._ROLES),
            (204, b"", None),
        ]
    )
    monkeypatch.setattr(subject, "_request", lambda *_args, **_kwargs: next(responses))

    receipt = subject.bootstrap("http://foundry-lite-keycloak:8080", _environment())

    assert receipt["status"] == "created"
    assert receipt["roles"] == list(subject._ROLES)


def test_bootstrap_rejects_cleartext_non_cluster_host() -> None:
    with pytest.raises(ValueError, match="keycloak_bootstrap_cleartext_host_forbidden"):
        subject.bootstrap("http://identity.example.com", _environment())


def test_bootstrap_rejects_redirects() -> None:
    with pytest.raises(RuntimeError, match="keycloak_bootstrap_redirect_not_allowed"):
        subject._NoRedirect().redirect_request(object(), object(), 302, "redirect", object(), "https://evil.invalid")
