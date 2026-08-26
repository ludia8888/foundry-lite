from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from scripts.operations import bootstrap_keycloak_qa_user as subject


def _environment() -> dict[str, str]:
    return {
        "KEYCLOAK_ADMIN": "qa-admin",
        "KEYCLOAK_ADMIN_PASSWORD": "admin-secret",
        "KEYCLOAK_QA_AUTHOR_USER": "author-1",
        "KEYCLOAK_QA_AUTHOR_USER_PASSWORD": "author-secret",
        "KEYCLOAK_QA_REVIEWER_USER": "reviewer-1",
        "KEYCLOAK_QA_REVIEWER_USER_PASSWORD": "reviewer-secret",
    }


def _role(role: str) -> bytes:
    return json.dumps({"id": f"id-{role}", "name": role}).encode()


def test_bootstrap_updates_distinct_existing_users_without_returning_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses: Iterator[tuple[int, bytes, str | None]] = iter(
        [
            (200, b'{"access_token":"raw-token"}', None),
            (200, b'[{"id":"user-1","username":"author-1"}]', None),
            (204, b"", None),
            (204, b"", None),
            *((200, _role(role), None) for role in subject._ROLES),
            (204, b"", None),
            (200, b'[{"id":"user-2","username":"reviewer-1"}]', None),
            (204, b"", None),
            (204, b"", None),
            *((200, _role(role), None) for role in subject._ROLES),
            (204, b"", None),
        ]
    )
    monkeypatch.setattr(subject, "_request", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(subject, "_ensure_runtime_claim_mappers", lambda _origin, _token: None)

    receipt = subject.bootstrap("http://foundry-lite-keycloak:8080", _environment())

    serialized = json.dumps(receipt)
    assert receipt["status"] == "updated"
    assert "admin-secret" not in serialized
    assert "author-secret" not in serialized
    assert "reviewer-secret" not in serialized
    assert "raw-token" not in serialized
    assert receipt["distinctSubjectsRequired"] is True
    assert [item["role"] for item in receipt["principals"]] == ["author", "reviewer"]


def test_bootstrap_creates_missing_users_then_resolves_exact_identities(monkeypatch: pytest.MonkeyPatch) -> None:
    responses: Iterator[tuple[int, bytes, str | None]] = iter(
        [
            (200, b'{"access_token":"raw-token"}', None),
            (200, b"[]", None),
            (201, b"", None),
            (200, b'[{"id":"user-1","username":"author-1"}]', None),
            (204, b"", None),
            (204, b"", None),
            *((200, _role(role), None) for role in subject._ROLES),
            (204, b"", None),
            (200, b"[]", None),
            (201, b"", None),
            (200, b'[{"id":"user-2","username":"reviewer-1"}]', None),
            (204, b"", None),
            (204, b"", None),
            *((200, _role(role), None) for role in subject._ROLES),
            (204, b"", None),
        ]
    )
    monkeypatch.setattr(subject, "_request", lambda *_args, **_kwargs: next(responses))
    monkeypatch.setattr(subject, "_ensure_runtime_claim_mappers", lambda _origin, _token: None)

    receipt = subject.bootstrap("http://foundry-lite-keycloak:8080", _environment())

    assert receipt["status"] == "created"
    assert all(item["roles"] == list(subject._ROLES) for item in receipt["principals"])


def test_bootstrap_user_profile_prevents_interactive_required_action() -> None:
    requests: list[tuple[str, str, object]] = []

    def request(_origin, path, method, payload, _token):
        requests.append((path, method, json.loads(payload)))
        return 204, b"", None

    original = subject._request
    try:
        subject._request = request
        subject._update_user_profile("http://foundry-lite-keycloak:8080", "token", "user-1", "author", "author-1")
    finally:
        subject._request = original

    profile = requests[0][2]
    assert profile["firstName"] == "Enterprise QA"
    assert profile["lastName"] == "Author"
    assert profile["emailVerified"] is True
    assert profile["requiredActions"] == []


def test_bootstrap_upserts_subject_and_role_mappers_for_lightweight_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_roles = {"id": "mapper-roles", "name": "foundry-lite-realm-roles"}
    responses = iter(
        (
            (200, b'[{"id":"scope-1","name":"foundry-lite-runtime"}]', None),
            (
                200,
                json.dumps(
                    {"id": "scope-1", "name": "foundry-lite-runtime", "protocolMappers": [current_roles]}
                ).encode(),
                None,
            ),
            (201, b"", None),
            (204, b"", None),
        )
    )
    calls: list[tuple[str, str, bytes | None]] = []

    def request(_origin, path, method, payload, _token):
        calls.append((path, method, payload))
        return next(responses)

    monkeypatch.setattr(subject, "_request", request)

    subject._ensure_runtime_claim_mappers("http://foundry-lite-keycloak:8080", "token")

    created = json.loads(calls[2][2])
    updated = json.loads(calls[3][2])
    assert created["protocolMapper"] == "oidc-sub-mapper"
    assert created["config"]["lightweight.claim"] == "true"
    assert updated["name"] == "foundry-lite-realm-roles"
    assert updated["id"] == "mapper-roles"
    assert updated["protocolMapper"] == "oidc-hardcoded-claim-mapper"
    assert json.loads(updated["config"]["claim.value"]) == list(subject._ROLES)
    assert updated["config"]["lightweight.claim"] == "true"


def test_bootstrap_rejects_same_author_and_reviewer_account() -> None:
    environment = _environment()
    environment["KEYCLOAK_QA_REVIEWER_USER"] = environment["KEYCLOAK_QA_AUTHOR_USER"]

    with pytest.raises(ValueError, match="principals_must_be_distinct"):
        subject.bootstrap("http://foundry-lite-keycloak:8080", environment)


def test_bootstrap_rejects_cleartext_non_cluster_host() -> None:
    with pytest.raises(ValueError, match="keycloak_bootstrap_cleartext_host_forbidden"):
        subject.bootstrap("http://identity.example.com", _environment())


def test_bootstrap_rejects_redirects() -> None:
    with pytest.raises(RuntimeError, match="keycloak_bootstrap_redirect_not_allowed"):
        subject._NoRedirect().redirect_request(object(), object(), 302, "redirect", object(), "https://evil.invalid")
