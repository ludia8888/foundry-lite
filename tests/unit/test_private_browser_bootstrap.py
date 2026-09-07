from __future__ import annotations

import json
from copy import deepcopy

import pytest

from scripts.operations.bootstrap_private_browser_login import _ensure_client, _ensure_redirect_host, _ensure_user
from scripts.operations.private_browser_keycloak import client_payload


class IdentityFixture:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def request(self, path, method="GET", payload=None, expected=200):
        self.calls.append((path, method, deepcopy(payload)))
        return next(self.responses)


def test_redirect_policy_preserves_existing_enforcers_and_adds_only_exact_host() -> None:
    profiles = {
        "profiles": [
            {
                "name": "foundry-lite-public-oauth",
                "executors": [
                    {
                        "executor": "secure-redirect-uris-enforcer",
                        "configuration": {
                            "allow-permitted-domains": [r"chatgpt\.com"],
                            "must-https": True,
                        },
                    },
                    {"executor": "pkce-enforcer", "configuration": {"auto-configure": True}},
                ],
            }
        ]
    }
    admin = IdentityFixture([deepcopy(profiles), None])
    _ensure_redirect_host(admin, "https://exact.trycloudflare.com")
    written = admin.calls[-1][2]
    profiles["profiles"][0]["executors"][0]["configuration"]["allow-permitted-domains"].append(
        r"exact\.trycloudflare\.com"
    )
    assert written == profiles
    replay = IdentityFixture([written])
    _ensure_redirect_host(replay, "https://exact.trycloudflare.com")
    assert len(replay.calls) == 1


def test_existing_client_without_private_owner_marker_is_never_overwritten() -> None:
    desired = client_payload("browser", "hospital", "tenant", "https://app.test", "api")
    admin = IdentityFixture([[{"id": "existing", "attributes": {}}]])
    with pytest.raises(RuntimeError, match="not_owned"):
        _ensure_client(admin, desired)
    assert len(admin.calls) == 1


def test_existing_owner_password_is_never_reset(tmp_path) -> None:
    admin = IdentityFixture([[{"id": "owner", "attributes": {"foundry_private_app": ["hospital"]}}]])
    assert _ensure_user(admin, "owner@example.test", "hospital", tmp_path, "https://app.test") == "owner"
    assert len(admin.calls) == 1
    assert list(tmp_path.iterdir()) == []


def test_other_existing_user_is_not_claimed_or_reset(tmp_path) -> None:
    admin = IdentityFixture([[{"id": "other", "attributes": {}}]])
    with pytest.raises(RuntimeError, match="requires_review"):
        _ensure_user(admin, "owner@example.test", "hospital", tmp_path, "https://app.test")
    assert len(admin.calls) == 1


def test_new_user_has_temporary_private_handoff_and_no_operator_roles(tmp_path) -> None:
    admin = IdentityFixture([[], None, [{"id": "new-owner"}]])
    assert _ensure_user(admin, "owner@example.test", "hospital", tmp_path, "https://app.test") == "new-owner"
    path = tmp_path / "first-login.json"
    assert path.stat().st_mode & 0o777 == 0o600
    handoff = json.loads(path.read_text())
    created = admin.calls[1][2]
    assert created["credentials"][0]["temporary"] is True
    assert created["credentials"][0]["value"] == handoff["initialPassword"]
    assert created["requiredActions"] == ["UPDATE_PASSWORD"]
    assert created["emailVerified"] is False
    assert "realmRoles" not in created
