"""The private-network SSRF bypass cannot be requested in protected profiles.

``allow_private_network`` disables the connector SSRF guard. It is only a
local-harness convenience, so a client request that enables it — connector
create/update or source explore — must be refused in production/staging, and no
routine role can flip the switch there. Setting it to ``False`` or leaving it
unset is always fine.
"""

from __future__ import annotations

import pytest
from foundry_lite.application.private_network_policy import (
    is_protected_runtime,
    private_network_bypass_permitted,
    require_private_network_bypass_allowed,
)
from foundry_lite.domain.errors import PermissionDenied

_PROTECTED = ["production", "prod", "staging", "stage"]
_LOCAL = ["local", "dev", "development", "demo", "test"]


@pytest.mark.parametrize("profile", _PROTECTED)
def test_enabling_bypass_is_denied_in_protected_runtime(profile: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_RUNTIME_PROFILE", profile)
    with pytest.raises(PermissionDenied, match="protected runtime profiles"):
        require_private_network_bypass_allowed(True, resource="erp_connector")


@pytest.mark.parametrize("profile", _LOCAL)
def test_enabling_bypass_is_allowed_in_local_runtime(profile: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_RUNTIME_PROFILE", profile)
    require_private_network_bypass_allowed(True, resource="erp_connector")  # no raise


@pytest.mark.parametrize("profile", _PROTECTED + _LOCAL)
@pytest.mark.parametrize("flag", [False, None])
def test_not_enabling_bypass_is_always_allowed(
    profile: str, flag: bool | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_RUNTIME_PROFILE", profile)
    require_private_network_bypass_allowed(flag, resource="erp_connector")  # no raise


def test_default_profile_is_not_protected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FOUNDRY_LITE_RUNTIME_PROFILE", raising=False)
    assert is_protected_runtime() is False
    require_private_network_bypass_allowed(True, resource="erp_connector")  # no raise


def test_bypass_permitted_reflects_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_RUNTIME_PROFILE", "production")
    assert private_network_bypass_permitted(True) is False
    monkeypatch.setenv("FOUNDRY_LITE_RUNTIME_PROFILE", "local")
    assert private_network_bypass_permitted(True) is True
    assert private_network_bypass_permitted(False) is False
