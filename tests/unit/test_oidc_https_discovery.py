from __future__ import annotations

import json

import pytest
from foundry_lite.infrastructure.auth.oidc_discovery import (
    HttpsOidcJwksLoader,
    OidcHttpsLoaderConfig,
    OidcJsonResponse,
    OidcJsonTransport,
)

ISSUER = "https://identity.example.test:8443/realms/foundry-lite"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
JWKS_URL = f"{ISSUER}/protocol/openid-connect/certs"


class _Transport(OidcJsonTransport):
    def __init__(self, responses: list[OidcJsonResponse]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, float, int]] = []

    def get(self, url: str, *, timeout_seconds: float, max_bytes: int) -> OidcJsonResponse:
        self.requests.append((url, timeout_seconds, max_bytes))
        return self.responses.pop(0)


def _config() -> OidcHttpsLoaderConfig:
    return OidcHttpsLoaderConfig(expected_issuer=ISSUER, discovery_url=DISCOVERY_URL)


def _response(payload: object, *, status_code: int = 200) -> OidcJsonResponse:
    return OidcJsonResponse(status_code, json.dumps(payload).encode())


def test_https_loader_pins_issuer_origin_and_refreshes_fixed_jwks_uri() -> None:
    transport = _Transport(
        [
            _response({"issuer": ISSUER, "jwks_uri": JWKS_URL}),
            _response({"keys": [{"kid": "key-1"}]}),
            _response({"keys": [{"kid": "key-2"}]}),
        ]
    )
    loader = HttpsOidcJwksLoader(_config(), transport=transport)

    authority = loader.initialize()
    refreshed = loader()

    assert authority.discovery["issuer"] == ISSUER
    assert authority.jwks["keys"] == [{"kid": "key-1"}]
    assert refreshed["keys"] == [{"kid": "key-2"}]
    assert [request[0] for request in transport.requests] == [DISCOVERY_URL, JWKS_URL, JWKS_URL]
    assert transport.requests[0][2] < transport.requests[1][2]


def test_https_loader_rejects_discovery_redirect_without_following_location() -> None:
    transport = _Transport([_response({}, status_code=302)])

    with pytest.raises(RuntimeError, match="oidc_discovery_fetch_failed"):
        HttpsOidcJwksLoader(_config(), transport=transport).initialize()

    assert len(transport.requests) == 1


def test_https_loader_rejects_issuer_mismatch() -> None:
    transport = _Transport([_response({"issuer": "https://other.example.test", "jwks_uri": JWKS_URL})])

    with pytest.raises(ValueError, match="oidc_discovery_issuer_mismatch"):
        HttpsOidcJwksLoader(_config(), transport=transport).initialize()


def test_https_loader_rejects_cross_origin_jwks_uri() -> None:
    transport = _Transport([_response({"issuer": ISSUER, "jwks_uri": "https://keys.attacker.example.test/jwks.json"})])

    with pytest.raises(ValueError, match="oidc_authority_origin_mismatch"):
        HttpsOidcJwksLoader(_config(), transport=transport).initialize()


@pytest.mark.parametrize(
    "discovery_url",
    [
        "http://identity.example.test/.well-known/openid-configuration",
        "https://user:password@identity.example.test/.well-known/openid-configuration",
        "https://identity.example.test/.well-known/openid-configuration?redirect=other",
    ],
)
def test_https_loader_rejects_non_https_credentials_and_query(discovery_url: str) -> None:
    with pytest.raises(ValueError):
        HttpsOidcJwksLoader(OidcHttpsLoaderConfig(ISSUER, discovery_url), transport=_Transport([]))


def test_https_loader_rejects_empty_jwks() -> None:
    transport = _Transport([_response({"issuer": ISSUER, "jwks_uri": JWKS_URL}), _response({"keys": []})])

    with pytest.raises(ValueError, match="oidc_jwks_keys_missing"):
        HttpsOidcJwksLoader(_config(), transport=transport).initialize()


@pytest.mark.parametrize("timeout_seconds", [float("nan"), float("inf"), float("-inf")])
def test_https_loader_rejects_non_finite_timeout(timeout_seconds: float) -> None:
    with pytest.raises(ValueError, match="oidc_https_timeout_invalid"):
        HttpsOidcJwksLoader(
            OidcHttpsLoaderConfig(ISSUER, DISCOVERY_URL, timeout_seconds=timeout_seconds),
            transport=_Transport([]),
        )
