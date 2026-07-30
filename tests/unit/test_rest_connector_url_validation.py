"""Unit tests for the REST connector SSRF / private-network URL guard.

These exercise the pure validation/parsing helpers (no DNS): IP literals,
legacy-encoded IPv4 forms, and private/local hostnames are all rejected before
any network resolution, while public IP literals are accepted.
"""

from __future__ import annotations

import pytest
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters.rest_connector import _validated_http_url


def test_accepts_public_ip_literal() -> None:
    assert _validated_http_url("https://8.8.8.8/path") == "https://8.8.8.8/path"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",  # non-http scheme
        "https:///path",  # missing host
    ],
)
def test_rejects_bad_scheme_or_missing_host(url: str) -> None:
    with pytest.raises(ValidationFailed, match="http"):
        _validated_http_url(url)


def test_rejects_invalid_port() -> None:
    with pytest.raises(ValidationFailed, match="port"):
        _validated_http_url("https://8.8.8.8:notaport/x")


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/x",  # local hostname
        "http://app.localhost/x",  # *.localhost
        "http://metadata.google.internal/x",  # cloud metadata hostname
        "http://service.internal/x",  # *.internal
        "http://printer.local/x",  # *.local
        "http://127.0.0.1/x",  # loopback literal
        "http://[::1]/x",  # ipv6 loopback literal
        "http://10.0.0.1/x",  # private
        "http://192.168.1.1/x",  # private
        "http://172.16.0.1/x",  # private
        "http://169.254.169.254/x",  # link-local (cloud metadata IP)
        "http://0.0.0.0/x",  # unspecified
        "http://224.0.0.1/x",  # multicast
        "http://2130706433/x",  # decimal-encoded 127.0.0.1
        "http://0x7f.0x0.0x0.0x1/x",  # hex-encoded 127.0.0.1 octets
        "http://0177.0.0.1/x",  # octal-encoded first octet (127)
    ],
)
def test_rejects_private_or_local_targets(url: str) -> None:
    with pytest.raises(ValidationFailed, match="private or local"):
        _validated_http_url(url)


def test_allows_private_when_explicitly_enabled() -> None:
    assert _validated_http_url("http://127.0.0.1/x", allow_private_network=True) == "http://127.0.0.1/x"


@pytest.mark.parametrize("profile", ["production", "prod", "staging", "stage"])
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata IP
        "http://127.0.0.1/x",  # loopback
        "http://10.0.0.1/x",  # private
    ],
)
def test_private_network_bypass_is_neutralized_in_protected_runtime(
    profile: str, url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The allow_private_network bypass is honored only outside protected profiles.

    The flag is a local-harness convenience. In production/staging it is forced
    off at this chokepoint so a connection created (and its flag stored) under a
    non-protected profile cannot open an SSRF path to internal hosts once run in
    production — even though the caller still passes allow_private_network=True.
    """
    monkeypatch.setenv("FOUNDRY_LITE_RUNTIME_PROFILE", profile)
    with pytest.raises(ValidationFailed, match="private or local"):
        _validated_http_url(url, allow_private_network=True)


@pytest.mark.parametrize("profile", ["local", "dev", "development", "demo", "test"])
def test_private_network_bypass_still_honored_in_local_profiles(profile: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FOUNDRY_LITE_RUNTIME_PROFILE", profile)
    assert _validated_http_url("http://127.0.0.1/x", allow_private_network=True) == "http://127.0.0.1/x"
