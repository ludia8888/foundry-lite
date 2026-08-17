"""Bounded HTTPS OIDC discovery and JWKS loading for external identity providers."""

from __future__ import annotations

import json
import math
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from http.client import HTTPException, HTTPSConnection
from typing import Protocol
from urllib.parse import SplitResult, urlsplit

_DEFAULT_DISCOVERY_MAX_BYTES = 256 * 1024
_DEFAULT_JWKS_MAX_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class OidcHttpsLoaderConfig:
    expected_issuer: str
    discovery_url: str
    timeout_seconds: float = 5.0
    discovery_max_bytes: int = _DEFAULT_DISCOVERY_MAX_BYTES
    jwks_max_bytes: int = _DEFAULT_JWKS_MAX_BYTES


@dataclass(frozen=True, slots=True)
class OidcJsonResponse:
    status_code: int
    body: bytes


@dataclass(frozen=True, slots=True)
class LoadedOidcAuthority:
    discovery: Mapping[str, object]
    jwks: Mapping[str, object]
    jwks_uri: str


class OidcJsonTransport(Protocol):
    def get(self, url: str, *, timeout_seconds: float, max_bytes: int) -> OidcJsonResponse: ...


class BoundedHttpsJsonTransport:
    """Fetch one HTTPS JSON document without redirects, credentials, or unbounded reads."""

    def get(self, url: str, *, timeout_seconds: float, max_bytes: int) -> OidcJsonResponse:
        parsed = _https_url(url, "oidc_https_url_invalid")
        port = parsed.port or 443
        host = parsed.hostname
        if host is None:
            raise ValueError("oidc_https_url_invalid")
        connection = HTTPSConnection(
            host,
            port,
            context=ssl.create_default_context(),
            timeout=timeout_seconds,
        )
        try:
            connection.request(
                "GET",
                _request_target(parsed),
                headers={"accept": "application/json", "user-agent": "Foundry-lite/oidc-discovery"},
            )
            response = connection.getresponse()
            payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise RuntimeError("oidc_https_response_too_large")
            return OidcJsonResponse(response.status, payload)
        except (HTTPException, OSError, TimeoutError, UnicodeError) as exc:
            raise RuntimeError("oidc_https_request_failed") from exc
        finally:
            connection.close()


class HttpsOidcJwksLoader:
    """Pin discovery and JWKS to one issuer origin and refresh only that JWKS URI."""

    def __init__(
        self,
        config: OidcHttpsLoaderConfig,
        *,
        transport: OidcJsonTransport | None = None,
    ) -> None:
        _validate_config(config)
        self._config = config
        self._transport = transport or BoundedHttpsJsonTransport()
        self._jwks_uri: str | None = None

    def initialize(self) -> LoadedOidcAuthority:
        discovery = self._fetch_json(
            self._config.discovery_url,
            max_bytes=self._config.discovery_max_bytes,
            reason="oidc_discovery_fetch_failed",
        )
        if discovery.get("issuer") != self._config.expected_issuer:
            raise ValueError("oidc_discovery_issuer_mismatch")
        jwks_uri = _required_string(discovery, "jwks_uri", "oidc_discovery_jwks_uri_missing")
        _require_same_https_origin(self._config.expected_issuer, jwks_uri)
        self._jwks_uri = jwks_uri
        jwks = self()
        return LoadedOidcAuthority(discovery=discovery, jwks=jwks, jwks_uri=jwks_uri)

    def __call__(self) -> Mapping[str, object]:
        if self._jwks_uri is None:
            raise RuntimeError("oidc_jwks_loader_not_initialized")
        jwks = self._fetch_json(
            self._jwks_uri,
            max_bytes=self._config.jwks_max_bytes,
            reason="oidc_jwks_fetch_failed",
        )
        keys = jwks.get("keys")
        if not isinstance(keys, list) or not keys:
            raise ValueError("oidc_jwks_keys_missing")
        return jwks

    def _fetch_json(self, url: str, *, max_bytes: int, reason: str) -> Mapping[str, object]:
        response = self._transport.get(
            url,
            timeout_seconds=self._config.timeout_seconds,
            max_bytes=max_bytes,
        )
        if response.status_code != 200:
            raise RuntimeError(reason)
        try:
            payload = json.loads(response.body)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(reason) from exc
        if not isinstance(payload, dict):
            raise ValueError(reason)
        return payload


def _validate_config(config: OidcHttpsLoaderConfig) -> None:
    _https_url(config.expected_issuer, "oidc_issuer_invalid")
    discovery = _https_url(config.discovery_url, "oidc_discovery_url_invalid")
    _require_same_https_origin(config.expected_issuer, config.discovery_url)
    if not discovery.path.endswith("/.well-known/openid-configuration"):
        raise ValueError("oidc_discovery_path_invalid")
    if not math.isfinite(config.timeout_seconds) or config.timeout_seconds <= 0 or config.timeout_seconds > 30:
        raise ValueError("oidc_https_timeout_invalid")
    for size in (config.discovery_max_bytes, config.jwks_max_bytes):
        if size < 1024 or size > 4 * 1024 * 1024:
            raise ValueError("oidc_https_size_limit_invalid")


def _require_same_https_origin(issuer: str, target: str) -> None:
    expected = _https_url(issuer, "oidc_issuer_invalid")
    actual = _https_url(target, "oidc_authority_url_invalid")
    if _origin(expected) != _origin(actual):
        raise ValueError("oidc_authority_origin_mismatch")


def _https_url(value: str, reason: str) -> SplitResult:
    parsed = urlsplit(value)
    if not _is_clean_https_url(parsed):
        raise ValueError(reason)
    _validate_port(parsed, reason)
    return parsed


def _is_clean_https_url(parsed: SplitResult) -> bool:
    return bool(
        parsed.scheme == "https"
        and parsed.hostname
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
        and not parsed.query
    )


def _validate_port(parsed: SplitResult, reason: str) -> None:
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(reason) from exc
    if port is not None and (port < 1 or port > 65535):
        raise ValueError(reason)


def _origin(parsed: SplitResult) -> tuple[str, int]:
    if parsed.hostname is None:
        raise ValueError("oidc_authority_url_invalid")
    return parsed.hostname.lower(), parsed.port or 443


def _request_target(parsed: SplitResult) -> str:
    return parsed.path or "/"


def _required_string(payload: Mapping[str, object], key: str, reason: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(reason)
    return value
