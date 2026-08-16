"""JWT/OIDC AuthProvider adapter backed by a local OIDC discovery document."""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Final, Literal, cast

import jwt
from jwt import PyJWTError
from jwt.algorithms import RSAAlgorithm

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.auth_provider import AuthProvider, Credentials, Principal
from foundry_lite.domain.errors import PermissionDenied
from foundry_lite.infrastructure.auth.oidc_discovery import HttpsOidcJwksLoader, OidcHttpsLoaderConfig

__all__ = [
    "AUTHORIZATION_HEADER",
    "OIDC_ALLOWED_CLIENT_IDS_JSON_ENV",
    "OIDC_AUDIENCE_ENV",
    "OIDC_CLIENT_ID_CLAIM_ENV",
    "OIDC_DISCOVERY_JSON_ENV",
    "OIDC_DISCOVERY_URL_ENV",
    "OIDC_GRANT_TYPE_CLAIM_ENV",
    "OIDC_GRANT_TYPE_VALUE_ENV",
    "OIDC_HUMAN_GRANT_CLAIM_ENV",
    "OIDC_HUMAN_GRANT_VALUE_ENV",
    "OIDC_ISSUER_ENV",
    "OIDC_JWKS_REFRESH_INTERVAL_SECONDS_ENV",
    "OIDC_JWKS_JSON_ENV",
    "OIDC_RETIRED_KEY_GRACE_SECONDS_ENV",
    "OIDC_REVOKED_JTIS_JSON_ENV",
    "OIDC_ROLES_CLAIM_ENV",
    "OIDC_SESSION_CLAIM_ENV",
    "OIDC_OSDK_APP_CLAIM_ENV",
    "OIDC_SCOPE_CLAIM_ENV",
    "OIDC_SERVICE_ACCOUNT_CLAIM_ENV",
    "OIDC_TENANT_CLAIM_ENV",
    "OIDC_USER_ATTRIBUTES_CLAIM_ENV",
    "JwtOidcAuthConfig",
    "JwtOidcAuthProvider",
    "jwt_oidc_auth_provider_from_env",
]

AUTHORIZATION_HEADER: Final = "authorization"
OIDC_DISCOVERY_JSON_ENV: Final = "FOUNDRY_LITE_OIDC_DISCOVERY_JSON"
OIDC_DISCOVERY_URL_ENV: Final = "FOUNDRY_LITE_OIDC_DISCOVERY_URL"
OIDC_HTTPS_TIMEOUT_SECONDS_ENV: Final = "FOUNDRY_LITE_OIDC_HTTPS_TIMEOUT_SECONDS"
OIDC_GRANT_TYPE_CLAIM_ENV: Final = "FOUNDRY_LITE_OIDC_GRANT_TYPE_CLAIM"
OIDC_GRANT_TYPE_VALUE_ENV: Final = "FOUNDRY_LITE_OIDC_GRANT_TYPE_VALUE"
OIDC_HUMAN_GRANT_CLAIM_ENV: Final = "FOUNDRY_LITE_OIDC_HUMAN_GRANT_CLAIM"
OIDC_HUMAN_GRANT_VALUE_ENV: Final = "FOUNDRY_LITE_OIDC_HUMAN_GRANT_VALUE"
OIDC_ISSUER_ENV: Final = "FOUNDRY_LITE_OIDC_ISSUER"
OIDC_AUDIENCE_ENV: Final = "FOUNDRY_LITE_OIDC_AUDIENCE"
OIDC_ALLOWED_CLIENT_IDS_JSON_ENV: Final = "FOUNDRY_LITE_OIDC_ALLOWED_CLIENT_IDS_JSON"
OIDC_CLIENT_ID_CLAIM_ENV: Final = "FOUNDRY_LITE_OIDC_CLIENT_ID_CLAIM"
OIDC_JWKS_JSON_ENV: Final = "FOUNDRY_LITE_OIDC_JWKS_JSON"
OIDC_JWKS_REFRESH_INTERVAL_SECONDS_ENV: Final = "FOUNDRY_LITE_OIDC_JWKS_REFRESH_INTERVAL_SECONDS"
OIDC_RETIRED_KEY_GRACE_SECONDS_ENV: Final = "FOUNDRY_LITE_OIDC_RETIRED_KEY_GRACE_SECONDS"
OIDC_REVOKED_JTIS_JSON_ENV: Final = "FOUNDRY_LITE_OIDC_REVOKED_JTIS_JSON"
OIDC_TENANT_CLAIM_ENV: Final = "FOUNDRY_LITE_OIDC_TENANT_CLAIM"
OIDC_ROLES_CLAIM_ENV: Final = "FOUNDRY_LITE_OIDC_ROLES_CLAIM"
OIDC_SESSION_CLAIM_ENV: Final = "FOUNDRY_LITE_OIDC_SESSION_CLAIM"
OIDC_SERVICE_ACCOUNT_CLAIM_ENV: Final = "FOUNDRY_LITE_OIDC_SERVICE_ACCOUNT_CLAIM"
OIDC_OSDK_APP_CLAIM_ENV: Final = "FOUNDRY_LITE_OIDC_OSDK_APP_CLAIM"
OIDC_SCOPE_CLAIM_ENV: Final = "FOUNDRY_LITE_OIDC_SCOPE_CLAIM"
OIDC_USER_ATTRIBUTES_CLAIM_ENV: Final = "FOUNDRY_LITE_OIDC_USER_ATTRIBUTES_CLAIM"

_DEFAULT_ALGORITHM: Final = "RS256"
_DEFAULT_TENANT_CLAIM: Final = "tenant_id"
_DEFAULT_ROLES_CLAIM: Final = "roles"
_DEFAULT_CLIENT_ID_CLAIM: Final = "client_id"
_DEFAULT_SERVICE_ACCOUNT_CLAIM: Final = "client_id"
_DEFAULT_OSDK_APP_CLAIM: Final = "osdk_app_id"
_DEFAULT_SCOPE_CLAIM: Final = "scope"
_DEFAULT_USER_ATTRIBUTES_CLAIM: Final = "user_attributes"
_OSDK_SESSION_CLAIM: Final = "foundry_lite_session_id"
_SERVICE_ACCOUNT_ACTOR_PREFIX: Final = "service-account:"
_JWT_ID_CLAIM: Final = "jti"
_SUBJECT_CLAIM: Final = "sub"
_JSON_REQUIRED: Final = object()

JwksLoader = Callable[[], Mapping[str, object]]
Clock = Callable[[], float]


@dataclass(frozen=True)
class _CachedJwk:
    key: Any
    retired_at: float | None = None


@dataclass(frozen=True)
class JwtOidcAuthConfig:
    """Minimal OIDC/JWT verification settings for the local production profile."""

    issuer: str
    audience: str
    jwks: Mapping[str, object]
    tenant_claim: str = _DEFAULT_TENANT_CLAIM
    roles_claim: str = _DEFAULT_ROLES_CLAIM
    client_id_claim: str = _DEFAULT_CLIENT_ID_CLAIM
    service_account_claim: str = _DEFAULT_SERVICE_ACCOUNT_CLAIM
    osdk_app_claim: str = _DEFAULT_OSDK_APP_CLAIM
    scope_claim: str = _DEFAULT_SCOPE_CLAIM
    user_attributes_claim: str = _DEFAULT_USER_ATTRIBUTES_CLAIM
    session_claim: str = _OSDK_SESSION_CLAIM
    oauth_session_authority: Literal["local", "issuer"] = "local"
    human_grant_claim: str | None = None
    human_grant_value: str | None = None
    grant_type_claim: str | None = None
    grant_type_value: str | None = None
    allowed_client_ids: frozenset[str] = field(default_factory=frozenset)
    revoked_token_ids: frozenset[str] = field(default_factory=frozenset)
    algorithm: str = _DEFAULT_ALGORITHM
    leeway_seconds: int = 0
    jwks_refresh_interval_seconds: int = 300
    retired_key_grace_seconds: int = 300


@dataclass
class JwtOidcAuthProvider:
    """Validate RS256 bearer tokens against issuer, audience, expiry, and JWKS."""

    config: JwtOidcAuthConfig
    jwks_loader: JwksLoader | None = None
    profile_name: str = "jwt-oidc-auth"
    clock: Clock = time.time
    _key_cache: dict[str, _CachedJwk] = field(default_factory=dict, init=False)
    _last_jwks_refresh_at: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self._merge_jwks(self.config.jwks)

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "authenticate",
                    "authentication",
                    False,
                    "Bearer token is missing, expired, malformed, tenantless, or fails "
                    "issuer/audience/signature/revocation validation.",
                ),
                AdapterFailureMode(
                    "refresh_jwks",
                    "unavailable",
                    True,
                    "JWKS refresh failed while validating an otherwise well-formed bearer token.",
                ),
            ),
        )

    def authenticate(self, credentials: Credentials) -> Principal:
        return self._authenticate(credentials, self.config.audience, is_exact_audience=False)

    def authenticate_for_audience(self, credentials: Credentials, audience: str) -> Principal:
        """Validate a token for one exact RFC 8707 protected resource."""

        return self._authenticate(credentials, audience, is_exact_audience=True)

    def _authenticate(self, credentials: Credentials, audience: str, *, is_exact_audience: bool) -> Principal:
        token = _bearer_token(credentials)
        if token is None:
            raise _permission_denied(self.profile_name, "missing_bearer_token")
        header = _unverified_header(token, self.profile_name)
        _require_algorithm(header, self.config.algorithm, self.profile_name)
        kid = _required_header(header, "kid", self.profile_name)
        key = self._key_for_kid(kid)
        payload = self._decode(token, key, audience)
        if is_exact_audience and payload.get("aud") != audience:
            raise _permission_denied(self.profile_name, "invalid_token", "ExactAudienceRequired")
        _reject_revoked_token(payload, self.config.revoked_token_ids, self.profile_name)
        actor_user_id = _actor_user_id(payload, self.config.service_account_claim, self.profile_name)
        client_id = _optional_payload_string(payload, self.config.client_id_claim)
        _require_allowed_client_id(self.config.allowed_client_ids, client_id, self.profile_name)
        raw_session_id = _optional_payload_string(payload, self.config.session_claim)
        oauth_session_id = _oauth_session_id(self.config, client_id, raw_session_id)
        oauth_session_hash = _oauth_session_hash(self.config, client_id, raw_session_id)
        return Principal(
            tenant_id=_required_payload_string(payload, self.config.tenant_claim, self.profile_name),
            actor_user_id=actor_user_id,
            roles=_roles_from_payload(payload, self.config.roles_claim, self.profile_name),
            application_id=_optional_payload_string(payload, self.config.osdk_app_claim),
            client_id=client_id,
            token_scopes=_scopes_from_payload(payload, self.config.scope_claim),
            oauth_session_id=oauth_session_id,
            oauth_session_hash=oauth_session_hash,
            oauth_session_authority=self.config.oauth_session_authority if oauth_session_id else None,
            authorization_server_issuer=self.config.issuer,
            oauth_grant_type=_oauth_grant_type(self.config, payload),
            oauth_resource=audience if is_exact_audience else None,
            oauth_token_issued_at=_numeric_date(payload, "iat", self.profile_name) if is_exact_audience else None,
            oauth_token_expires_at=_numeric_date(payload, "exp", self.profile_name) if is_exact_audience else None,
            is_human_oauth=_is_human_oauth(self.config, payload, actor_user_id, raw_session_id),
            user_attributes=_user_attributes_from_payload(
                payload,
                self.config.user_attributes_claim,
                self.profile_name,
            ),
        )

    def anonymous(self) -> Principal:
        raise _permission_denied(self.profile_name, "anonymous_not_allowed")

    def _decode(self, token: str, key: Any, audience: str) -> dict[str, object]:
        try:
            payload = jwt.decode(
                token,
                key=key,
                algorithms=[self.config.algorithm],
                audience=audience,
                issuer=self.config.issuer,
                leeway=self.config.leeway_seconds,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
        except PyJWTError as exc:
            raise _permission_denied(self.profile_name, "invalid_token", exc.__class__.__name__) from exc
        return cast(dict[str, object], payload)

    def _key_for_kid(self, kid: str) -> Any:
        self._refresh_jwks_if_stale()
        cached = self._cached_key(kid)
        if cached is not None:
            return cached
        if self.jwks_loader is not None:
            self._refresh_jwks()
        cached = self._cached_key(kid)
        if cached is None:
            raise _permission_denied(self.profile_name, "unknown_key_id")
        return cached

    def _merge_jwks(self, jwks: Mapping[str, object]) -> None:
        now = self.clock()
        incoming_kids: set[str] = set()
        for key_data in _jwks_keys(jwks):
            kid = _required_jwk_string(key_data, "kid")
            incoming_kids.add(kid)
            self._key_cache[kid] = _CachedJwk(RSAAlgorithm.from_jwk(json.dumps(dict(key_data))))
        for kid, cached in tuple(self._key_cache.items()):
            if kid in incoming_kids:
                continue
            if cached.retired_at is None:
                self._key_cache[kid] = _CachedJwk(cached.key, retired_at=now)
            elif self._is_retired_key_expired(cached, now):
                del self._key_cache[kid]
        self._last_jwks_refresh_at = now

    def _refresh_jwks_if_stale(self) -> None:
        if self.jwks_loader is None:
            return
        elapsed = self.clock() - self._last_jwks_refresh_at
        if elapsed >= self.config.jwks_refresh_interval_seconds:
            self._refresh_jwks()

    def _refresh_jwks(self) -> None:
        if self.jwks_loader is not None:
            self._merge_jwks(self.jwks_loader())

    def _cached_key(self, kid: str) -> Any | None:
        cached = self._key_cache.get(kid)
        if cached is None:
            return None
        if self._is_retired_key_expired(cached, self.clock()):
            del self._key_cache[kid]
            return None
        return cached.key

    def _is_retired_key_expired(self, cached: _CachedJwk, now: float) -> bool:
        return cached.retired_at is not None and now - cached.retired_at > self.config.retired_key_grace_seconds


def jwt_oidc_auth_provider_from_env(environ: Mapping[str, str] | None = None) -> JwtOidcAuthProvider:
    """Build a JWT/OIDC provider from environment-backed local discovery data."""
    source = os.environ if environ is None else environ
    discovery_url = _env_value(source, OIDC_DISCOVERY_URL_ENV)
    if discovery_url is None:
        config = _oidc_config_from_env(source)
        return JwtOidcAuthProvider(config=config, jwks_loader=_static_jwks_loader(source))
    issuer = _required_oidc_value(_env_value(source, OIDC_ISSUER_ENV), OIDC_ISSUER_ENV)
    remote_loader = HttpsOidcJwksLoader(
        OidcHttpsLoaderConfig(
            expected_issuer=issuer,
            discovery_url=discovery_url,
            timeout_seconds=_float_from_env(source, OIDC_HTTPS_TIMEOUT_SECONDS_ENV, 5.0),
        )
    )
    authority = remote_loader.initialize()
    config = _oidc_config_from_env(source, discovery=authority.discovery, jwks=authority.jwks)
    return JwtOidcAuthProvider(config=config, jwks_loader=remote_loader)


def _static_jwks_loader(source: Mapping[str, str]) -> JwksLoader:
    def load() -> Mapping[str, object]:
        return _json_object_from_env(source, OIDC_JWKS_JSON_ENV)

    return load


def _oidc_config_from_env(
    source: Mapping[str, str],
    *,
    discovery: Mapping[str, object] | None = None,
    jwks: Mapping[str, object] | None = None,
) -> JwtOidcAuthConfig:
    """Resolve one immutable OIDC adapter configuration from environment values."""
    resolved_discovery = discovery or _json_object_from_env(source, OIDC_DISCOVERY_JSON_ENV, default={})
    issuer = _required_oidc_value(
        _env_value(source, OIDC_ISSUER_ENV) or _object_string(resolved_discovery, "issuer"),
        f"{OIDC_ISSUER_ENV} or discovery issuer",
    )
    audience = _required_oidc_value(_env_value(source, OIDC_AUDIENCE_ENV), OIDC_AUDIENCE_ENV)
    resolved_jwks = jwks or _json_object_from_env(source, OIDC_JWKS_JSON_ENV)
    human_grant_claim, human_grant_value = _human_grant_pair(source)
    grant_type_claim, grant_type_value = _grant_type_pair(source)
    return JwtOidcAuthConfig(
        issuer=issuer,
        audience=audience,
        jwks=resolved_jwks,
        tenant_claim=_oidc_claim(source, OIDC_TENANT_CLAIM_ENV, _DEFAULT_TENANT_CLAIM),
        roles_claim=_oidc_claim(source, OIDC_ROLES_CLAIM_ENV, _DEFAULT_ROLES_CLAIM),
        client_id_claim=_oidc_claim(source, OIDC_CLIENT_ID_CLAIM_ENV, _DEFAULT_CLIENT_ID_CLAIM),
        service_account_claim=_oidc_claim(source, OIDC_SERVICE_ACCOUNT_CLAIM_ENV, _DEFAULT_SERVICE_ACCOUNT_CLAIM),
        osdk_app_claim=_oidc_claim(source, OIDC_OSDK_APP_CLAIM_ENV, _DEFAULT_OSDK_APP_CLAIM),
        scope_claim=_oidc_claim(source, OIDC_SCOPE_CLAIM_ENV, _DEFAULT_SCOPE_CLAIM),
        user_attributes_claim=_oidc_claim(
            source,
            OIDC_USER_ATTRIBUTES_CLAIM_ENV,
            _DEFAULT_USER_ATTRIBUTES_CLAIM,
        ),
        session_claim=_oidc_claim(source, OIDC_SESSION_CLAIM_ENV, _OSDK_SESSION_CLAIM),
        oauth_session_authority="issuer",
        human_grant_claim=human_grant_claim,
        human_grant_value=human_grant_value,
        grant_type_claim=grant_type_claim,
        grant_type_value=grant_type_value,
        allowed_client_ids=_json_string_set_from_env(source, OIDC_ALLOWED_CLIENT_IDS_JSON_ENV),
        revoked_token_ids=_json_string_set_from_env(source, OIDC_REVOKED_JTIS_JSON_ENV),
        jwks_refresh_interval_seconds=_int_from_env(source, OIDC_JWKS_REFRESH_INTERVAL_SECONDS_ENV, 300),
        retired_key_grace_seconds=_int_from_env(source, OIDC_RETIRED_KEY_GRACE_SECONDS_ENV, 300),
    )


def _required_oidc_value(value: str | None, setting: str) -> str:
    if value is None:
        raise ValueError(f"{setting} is required for JWT/OIDC auth")
    return value


def _human_grant_pair(source: Mapping[str, str]) -> tuple[str | None, str | None]:
    claim = _env_value(source, OIDC_HUMAN_GRANT_CLAIM_ENV)
    value = _env_value(source, OIDC_HUMAN_GRANT_VALUE_ENV)
    if (claim is None) != (value is None):
        raise ValueError(f"{OIDC_HUMAN_GRANT_CLAIM_ENV} and {OIDC_HUMAN_GRANT_VALUE_ENV} must be configured together")
    return claim, value


def _grant_type_pair(source: Mapping[str, str]) -> tuple[str | None, str | None]:
    claim = _env_value(source, OIDC_GRANT_TYPE_CLAIM_ENV)
    value = _env_value(source, OIDC_GRANT_TYPE_VALUE_ENV)
    if (claim is None) != (value is None):
        raise ValueError(f"{OIDC_GRANT_TYPE_CLAIM_ENV} and {OIDC_GRANT_TYPE_VALUE_ENV} must be configured together")
    return claim, value


def _oidc_claim(source: Mapping[str, str], setting: str, default: str) -> str:
    return _env_value(source, setting) or default


def _bearer_token(credentials: Credentials) -> str | None:
    credentials_by_key = {key.lower(): value for key, value in credentials.items()}
    authorization = credentials_by_key.get(AUTHORIZATION_HEADER, "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token.strip() == "":
        return None
    return token.strip()


def _unverified_header(token: str, profile_name: str) -> dict[str, object]:
    try:
        return cast(dict[str, object], jwt.get_unverified_header(token))
    except PyJWTError as exc:
        raise _permission_denied(profile_name, "invalid_header", exc.__class__.__name__) from exc


def _require_algorithm(header: Mapping[str, object], expected_algorithm: str, profile_name: str) -> None:
    algorithm = _required_header(header, "alg", profile_name)
    if algorithm != expected_algorithm:
        raise _permission_denied(profile_name, "unsupported_algorithm")


def _required_header(header: Mapping[str, object], name: str, profile_name: str) -> str:
    value = header.get(name)
    if not isinstance(value, str) or value.strip() == "":
        raise _permission_denied(profile_name, f"missing_{name}")
    return value


def _required_payload_string(payload: Mapping[str, object], claim: str, profile_name: str) -> str:
    value = _optional_payload_string(payload, claim)
    if value is None:
        raise _permission_denied(profile_name, f"missing_{claim}")
    return value


def _optional_payload_string(payload: Mapping[str, object], claim: str) -> str | None:
    value = payload.get(claim)
    if not isinstance(value, str) or value.strip() == "":
        return None
    return value.strip()


def _actor_user_id(payload: Mapping[str, object], service_account_claim: str, profile_name: str) -> str:
    subject = _optional_payload_string(payload, _SUBJECT_CLAIM)
    if subject is not None:
        return subject
    service_account_id = _optional_payload_string(payload, service_account_claim)
    if service_account_id is None:
        raise _permission_denied(profile_name, "missing_sub_or_service_account")
    return f"{_SERVICE_ACCOUNT_ACTOR_PREFIX}{service_account_id}"


def _oauth_session_id(
    config: JwtOidcAuthConfig,
    client_id: str | None,
    raw_session_id: str | None,
) -> str | None:
    if raw_session_id is None:
        return None
    if config.oauth_session_authority == "local":
        return raw_session_id
    if client_id is None:
        return None
    material = "\x1f".join((config.issuer, client_id, raw_session_id))
    return f"issuer-session:{sha256(material.encode()).hexdigest()}"


def _oauth_session_hash(
    config: JwtOidcAuthConfig,
    client_id: str | None,
    raw_session_id: str | None,
) -> str | None:
    if client_id is None or raw_session_id is None:
        return None
    material = "\x1f".join((config.issuer, client_id, raw_session_id))
    return f"oauth-session:sha256:{sha256(material.encode()).hexdigest()}"


def _oauth_grant_type(config: JwtOidcAuthConfig, payload: Mapping[str, object]) -> Literal["authorization_code"] | None:
    claim = config.grant_type_claim
    expected = config.grant_type_value
    if claim and expected and payload.get(claim) == expected:
        return "authorization_code"
    return None


def _numeric_date(payload: Mapping[str, object], claim: str, profile_name: str) -> int:
    value = payload.get(claim)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _permission_denied(profile_name, "invalid_token", f"Invalid{claim.title()}")
    return int(value)


def _require_allowed_client_id(
    allowed_client_ids: frozenset[str],
    client_id: str | None,
    profile_name: str,
) -> None:
    if allowed_client_ids and client_id not in allowed_client_ids:
        raise _permission_denied(profile_name, "client_id_not_allowed")


def _is_human_oauth(
    config: JwtOidcAuthConfig,
    payload: Mapping[str, object],
    actor_user_id: str,
    raw_session_id: str | None,
) -> bool:
    if raw_session_id is None or _is_machine_actor(actor_user_id):
        return False
    if config.oauth_session_authority == "local":
        return True
    claim = config.human_grant_claim
    expected = config.human_grant_value
    return bool(claim and expected and payload.get(claim) == expected)


def _is_machine_actor(actor_user_id: str) -> bool:
    return actor_user_id.startswith(("service-account:", "service-principal:"))


def _reject_revoked_token(
    payload: Mapping[str, object],
    revoked_token_ids: frozenset[str],
    profile_name: str,
) -> None:
    if not revoked_token_ids:
        return
    jwt_id = _optional_payload_string(payload, _JWT_ID_CLAIM)
    if jwt_id in revoked_token_ids:
        raise _permission_denied(profile_name, "revoked_token_id")


def _roles_from_payload(payload: Mapping[str, object], claim: str, profile_name: str) -> tuple[str, ...]:
    value = payload.get(claim)
    roles = (
        _string_sequence(value) if isinstance(value, Sequence) and not isinstance(value, str) else _string_roles(value)
    )
    if not roles:
        raise _permission_denied(profile_name, f"missing_{claim}")
    return roles


def _scopes_from_payload(payload: Mapping[str, object], claim: str) -> tuple[str, ...]:
    value = payload.get(claim)
    if value is None and claim != "scp":
        value = payload.get("scp")
    if isinstance(value, Sequence) and not isinstance(value, str):
        return _string_sequence(value)
    return _string_roles(value)


def _user_attributes_from_payload(payload: Mapping[str, object], claim: str, profile_name: str) -> dict[str, object]:
    value = payload.get(claim)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise _permission_denied(profile_name, f"invalid_{claim}")
    attributes = cast(Mapping[object, object], value)
    if not all(isinstance(key, str) and key.strip() for key in attributes):
        raise _permission_denied(profile_name, f"invalid_{claim}")
    return {str(key): item for key, item in attributes.items()}


def _string_sequence(value: Sequence[object]) -> tuple[str, ...]:
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _string_roles(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    separator = "," if "," in value else " "
    return tuple(role.strip() for role in value.split(separator) if role.strip())


def _jwks_keys(jwks: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    keys = jwks.get("keys")
    if not isinstance(keys, list):
        raise ValueError("JWKS must contain a keys list")
    return tuple(key for key in keys if isinstance(key, Mapping))


def _required_jwk_string(key_data: Mapping[str, object], name: str) -> str:
    value = key_data.get(name)
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(f"JWK is missing {name}")
    return value


def _json_object_from_env(
    environ: Mapping[str, str],
    name: str,
    *,
    default: object = _JSON_REQUIRED,
) -> Mapping[str, object]:
    raw = _env_value(environ, name)
    if raw is None:
        if default is _JSON_REQUIRED:
            raise ValueError(f"{name} is required")
        return cast(Mapping[str, object], default)
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must be a JSON object")
    return cast(Mapping[str, object], parsed)


def _json_string_set_from_env(environ: Mapping[str, str], name: str) -> frozenset[str]:
    raw = _env_value(environ, name)
    if raw is None:
        return frozenset()
    parsed = json.loads(raw)
    if not isinstance(parsed, list):
        raise ValueError(f"{name} must be a JSON array")
    values: set[str] = set()
    for item in parsed:
        if not isinstance(item, str) or item.strip() == "":
            raise ValueError(f"{name} must contain only non-empty strings")
        values.add(item.strip())
    return frozenset(values)


def _int_from_env(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = _env_value(environ, name)
    if raw is None:
        return default
    value = int(raw)
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _float_from_env(environ: Mapping[str, str], name: str, default: float) -> float:
    raw = _env_value(environ, name)
    if raw is None:
        return default
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _env_value(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name)
    if value is None or value.strip() == "":
        return None
    return value.strip()


def _object_string(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    if not isinstance(item, str) or item.strip() == "":
        return None
    return item.strip()


def _permission_denied(profile_name: str, reason: str, error_type: str | None = None) -> PermissionDenied:
    details: dict[str, object] = {"auth_profile": profile_name, "reason": reason}
    if error_type is not None:
        details["error_type"] = error_type
    return PermissionDenied("authentication failed", details=details)


_: AuthProvider = JwtOidcAuthProvider(
    JwtOidcAuthConfig(
        issuer="https://issuer.example.test",
        audience="foundry-lite",
        jwks={"keys": []},
    )
)
