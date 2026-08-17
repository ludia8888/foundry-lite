from __future__ import annotations

import json

import pytest
from foundry_lite.infrastructure.auth import oidc_discovery
from foundry_lite.infrastructure.auth.oidc_discovery import (
    BoundedHttpsJsonTransport,
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


def test_https_loader_requires_initialization_and_object_json() -> None:
    loader = HttpsOidcJwksLoader(_config(), transport=_Transport([]))
    with pytest.raises(RuntimeError, match="oidc_jwks_loader_not_initialized"):
        loader()

    invalid_json = _Transport([OidcJsonResponse(200, b"not-json")])
    with pytest.raises(ValueError, match="oidc_discovery_fetch_failed"):
        HttpsOidcJwksLoader(_config(), transport=invalid_json).initialize()

    array_json = _Transport([_response([])])
    with pytest.raises(ValueError, match="oidc_discovery_fetch_failed"):
        HttpsOidcJwksLoader(_config(), transport=array_json).initialize()


def test_https_loader_requires_jwks_uri_and_successful_jwks_response() -> None:
    missing = _Transport([_response({"issuer": ISSUER})])
    with pytest.raises(ValueError, match="oidc_discovery_jwks_uri_missing"):
        HttpsOidcJwksLoader(_config(), transport=missing).initialize()

    failed_jwks = _Transport([_response({"issuer": ISSUER, "jwks_uri": JWKS_URL}), _response({}, status_code=503)])
    with pytest.raises(RuntimeError, match="oidc_jwks_fetch_failed"):
        HttpsOidcJwksLoader(_config(), transport=failed_jwks).initialize()


@pytest.mark.parametrize(
    "config",
    [
        OidcHttpsLoaderConfig(ISSUER, f"{ISSUER}/configuration"),
        OidcHttpsLoaderConfig(ISSUER, DISCOVERY_URL, timeout_seconds=0),
        OidcHttpsLoaderConfig(ISSUER, DISCOVERY_URL, timeout_seconds=31),
        OidcHttpsLoaderConfig(ISSUER, DISCOVERY_URL, discovery_max_bytes=100),
        OidcHttpsLoaderConfig(ISSUER, DISCOVERY_URL, jwks_max_bytes=5 * 1024 * 1024),
        OidcHttpsLoaderConfig("https://identity.example.test:99999/issuer", DISCOVERY_URL),
    ],
)
def test_https_loader_rejects_unsafe_bounds(config: OidcHttpsLoaderConfig) -> None:
    with pytest.raises(ValueError):
        HttpsOidcJwksLoader(config, transport=_Transport([]))


def test_bounded_https_transport_sends_one_request_without_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        status = 200

        def read(self, size: int) -> bytes:
            assert size == 1025
            return b'{"issuer":"ok"}'

    class _Connection:
        def __init__(self) -> None:
            self.request_args: tuple[object, ...] | None = None
            self.is_closed = False

        def request(self, *args: object, **kwargs: object) -> None:
            self.request_args = (*args, kwargs)

        def getresponse(self) -> _Response:
            return _Response()

        def close(self) -> None:
            self.is_closed = True

    connection = _Connection()
    monkeypatch.setattr(oidc_discovery.ssl, "create_default_context", lambda: object())
    monkeypatch.setattr(oidc_discovery, "HTTPSConnection", lambda *_args, **_kwargs: connection)

    response = BoundedHttpsJsonTransport().get(
        "https://identity.example.test:8443/discovery",
        timeout_seconds=2,
        max_bytes=1024,
    )

    assert response == OidcJsonResponse(200, b'{"issuer":"ok"}')
    assert connection.request_args is not None
    assert connection.request_args[:2] == ("GET", "/discovery")
    assert connection.is_closed


def test_bounded_https_transport_rejects_large_or_failed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _LargeResponse:
        status = 200

        def read(self, _size: int) -> bytes:
            return b"x" * 1025

    class _Connection:
        def __init__(self, failure: BaseException | None = None) -> None:
            self.failure = failure

        def request(self, *_args: object, **_kwargs: object) -> None:
            if self.failure is not None:
                raise self.failure

        def getresponse(self) -> _LargeResponse:
            return _LargeResponse()

        def close(self) -> None:
            pass

    monkeypatch.setattr(oidc_discovery.ssl, "create_default_context", lambda: object())
    monkeypatch.setattr(oidc_discovery, "HTTPSConnection", lambda *_args, **_kwargs: _Connection())
    with pytest.raises(RuntimeError, match="oidc_https_response_too_large"):
        BoundedHttpsJsonTransport().get(DISCOVERY_URL, timeout_seconds=2, max_bytes=1024)

    monkeypatch.setattr(
        oidc_discovery,
        "HTTPSConnection",
        lambda *_args, **_kwargs: _Connection(OSError("private-network-detail")),
    )
    with pytest.raises(RuntimeError, match="oidc_https_request_failed") as captured:
        BoundedHttpsJsonTransport().get(DISCOVERY_URL, timeout_seconds=2, max_bytes=1024)
    assert "private-network-detail" not in str(captured.value)


def test_bounded_https_transport_rejects_unclean_url_before_network() -> None:
    with pytest.raises(ValueError, match="oidc_https_url_invalid"):
        BoundedHttpsJsonTransport().get("http://identity.example.test", timeout_seconds=2, max_bytes=1024)
