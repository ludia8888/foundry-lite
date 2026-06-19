"""JWT/OIDC AuthProvider adapter backed by a local OIDC discovery document."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, cast

import jwt
from jwt import PyJWTError
from jwt.algorithms import RSAAlgorithm

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.auth_provider import AuthProvider, Credentials, Principal
from foundry_lite.domain.errors import PermissionDenied

__all__ = [
    "AUTHORIZATION_HEADER",
    "OIDC_AUDIENCE_ENV",
    "OIDC_DISCOVERY_JSON_ENV",
    "OIDC_ISSUER_ENV",
    "OIDC_JWKS_JSON_ENV",
    "OIDC_REVOKED_JTIS_JSON_ENV",
    "OIDC_ROLES_CLAIM_ENV",
    "OIDC_SERVICE_ACCOUNT_CLAIM_ENV",
    "OIDC_TENANT_CLAIM_ENV",
    "JwtOidcAuthConfig",
    "JwtOidcAuthProvider",
    "jwt_oidc_auth_provider_from_env",
]

AUTHORIZATION_HEADER: Final = "authorization"
OIDC_DISCOVERY_JSON_ENV: Final = "FOUNDRY_LITE_OIDC_DISCOVERY_JSON"
OIDC_ISSUER_ENV: Final = "FOUNDRY_LITE_OIDC_ISSUER"
OIDC_AUDIENCE_ENV: Final = "FOUNDRY_LITE_OIDC_AUDIENCE"
OIDC_JWKS_JSON_ENV: Final = "FOUNDRY_LITE_OIDC_JWKS_JSON"
OIDC_REVOKED_JTIS_JSON_ENV: Final = "FOUNDRY_LITE_OIDC_REVOKED_JTIS_JSON"
OIDC_TENANT_CLAIM_ENV: Final = "FOUNDRY_LITE_OIDC_TENANT_CLAIM"
OIDC_ROLES_CLAIM_ENV: Final = "FOUNDRY_LITE_OIDC_ROLES_CLAIM"
OIDC_SERVICE_ACCOUNT_CLAIM_ENV: Final = "FOUNDRY_LITE_OIDC_SERVICE_ACCOUNT_CLAIM"

_DEFAULT_ALGORITHM: Final = "RS256"
_DEFAULT_TENANT_CLAIM: Final = "tenant_id"
_DEFAULT_ROLES_CLAIM: Final = "roles"
_DEFAULT_SERVICE_ACCOUNT_CLAIM: Final = "client_id"
_SERVICE_ACCOUNT_ACTOR_PREFIX: Final = "service-account:"
_JWT_ID_CLAIM: Final = "jti"
_SUBJECT_CLAIM: Final = "sub"
_JSON_REQUIRED: Final = object()

JwksLoader = Callable[[], Mapping[str, object]]


@dataclass(frozen=True)
class JwtOidcAuthConfig:
    """Minimal OIDC/JWT verification settings for the local production profile."""

    issuer: str
    audience: str
    jwks: Mapping[str, object]
    tenant_claim: str = _DEFAULT_TENANT_CLAIM
    roles_claim: str = _DEFAULT_ROLES_CLAIM
    service_account_claim: str = _DEFAULT_SERVICE_ACCOUNT_CLAIM
    revoked_token_ids: frozenset[str] = field(default_factory=frozenset)
    algorithm: str = _DEFAULT_ALGORITHM
    leeway_seconds: int = 0


@dataclass
class JwtOidcAuthProvider:
    """Validate RS256 bearer tokens against issuer, audience, expiry, and JWKS."""

    config: JwtOidcAuthConfig
    jwks_loader: JwksLoader | None = None
    profile_name: str = "jwt-oidc-auth"
    _key_cache: dict[str, Any] = field(default_factory=dict, init=False)

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
        token = _bearer_token(credentials)
        if token is None:
            raise _permission_denied(self.profile_name, "missing_bearer_token")
        header = _unverified_header(token, self.profile_name)
        _require_algorithm(header, self.config.algorithm, self.profile_name)
        kid = _required_header(header, "kid", self.profile_name)
        key = self._key_for_kid(kid)
        payload = self._decode(token, key)
        _reject_revoked_token(payload, self.config.revoked_token_ids, self.profile_name)
        return Principal(
            tenant_id=_required_payload_string(payload, self.config.tenant_claim, self.profile_name),
            actor_user_id=_actor_user_id(payload, self.config.service_account_claim, self.profile_name),
            roles=_roles_from_payload(payload, self.config.roles_claim, self.profile_name),
        )

    def anonymous(self) -> Principal:
        raise _permission_denied(self.profile_name, "anonymous_not_allowed")

    def _decode(self, token: str, key: Any) -> dict[str, object]:
        try:
            payload = jwt.decode(
                token,
                key=key,
                algorithms=[self.config.algorithm],
                audience=self.config.audience,
                issuer=self.config.issuer,
                leeway=self.config.leeway_seconds,
            )
        except PyJWTError as exc:
            raise _permission_denied(self.profile_name, "invalid_token", exc.__class__.__name__) from exc
        return cast(dict[str, object], payload)

    def _key_for_kid(self, kid: str) -> Any:
        if kid in self._key_cache:
            return self._key_cache[kid]
        if self.jwks_loader is not None:
            self._merge_jwks(self.jwks_loader())
        if kid not in self._key_cache:
            raise _permission_denied(self.profile_name, "unknown_key_id")
        return self._key_cache[kid]

    def _merge_jwks(self, jwks: Mapping[str, object]) -> None:
        for key_data in _jwks_keys(jwks):
            kid = _required_jwk_string(key_data, "kid")
            self._key_cache[kid] = RSAAlgorithm.from_jwk(json.dumps(dict(key_data)))


def jwt_oidc_auth_provider_from_env(environ: Mapping[str, str] | None = None) -> JwtOidcAuthProvider:
    """Build a JWT/OIDC provider from environment-backed local discovery data."""
    source = os.environ if environ is None else environ
    discovery = _json_object_from_env(source, OIDC_DISCOVERY_JSON_ENV, default={})
    issuer = _env_value(source, OIDC_ISSUER_ENV) or _object_string(discovery, "issuer")
    audience = _env_value(source, OIDC_AUDIENCE_ENV)
    jwks = _json_object_from_env(source, OIDC_JWKS_JSON_ENV)
    if not issuer:
        raise ValueError(f"{OIDC_ISSUER_ENV} or discovery issuer is required for JWT/OIDC auth")
    if not audience:
        raise ValueError(f"{OIDC_AUDIENCE_ENV} is required for JWT/OIDC auth")
    config = JwtOidcAuthConfig(
        issuer=issuer,
        audience=audience,
        jwks=jwks,
        tenant_claim=_env_value(source, OIDC_TENANT_CLAIM_ENV) or _DEFAULT_TENANT_CLAIM,
        roles_claim=_env_value(source, OIDC_ROLES_CLAIM_ENV) or _DEFAULT_ROLES_CLAIM,
        service_account_claim=_env_value(source, OIDC_SERVICE_ACCOUNT_CLAIM_ENV) or _DEFAULT_SERVICE_ACCOUNT_CLAIM,
        revoked_token_ids=_json_string_set_from_env(source, OIDC_REVOKED_JTIS_JSON_ENV),
    )
    return JwtOidcAuthProvider(config=config, jwks_loader=lambda: _json_object_from_env(source, OIDC_JWKS_JSON_ENV))


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
