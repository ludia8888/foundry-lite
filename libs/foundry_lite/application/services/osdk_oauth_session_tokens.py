"""Pure PKCE, opaque-token, expiry, and redirect helpers for local OSDK OAuth."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from foundry_lite.application.ports import RuntimeJsonObject
from foundry_lite.domain.errors import ValidationFailed

_PKCE_METHODS = frozenset({"S256"})
_CODE_CHALLENGE_MIN_LENGTH = 43
_CODE_CHALLENGE_MAX_LENGTH = 128
_CODE_CHALLENGE_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


def pkce_method(method: str) -> str:
    if method not in _PKCE_METHODS:
        raise ValidationFailed("OSDK OAuth code_challenge_method must be S256")
    return method


def require_valid_code_challenge(code_challenge: str) -> None:
    length = len(code_challenge)
    if (
        length < _CODE_CHALLENGE_MIN_LENGTH
        or length > _CODE_CHALLENGE_MAX_LENGTH
        or _CODE_CHALLENGE_PATTERN.fullmatch(code_challenge) is None
    ):
        raise ValidationFailed("OSDK OAuth code_challenge must be 43-128 base64url characters")


def pkce_matches(method: str, challenge: str, verifier: str) -> bool:
    if method != "S256":
        return False
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(challenge, encoded)


def raw_token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def raw_authorization_code(resource: str | None) -> str:
    if resource is None:
        return raw_token("oauth_code")
    fingerprint = hashlib.sha256(resource.encode("utf-8")).hexdigest()
    return f"oauth_code_resource_{fingerprint}_{secrets.token_urlsafe(32)}"


def authorization_code_matches_resource(code: str, resource: str | None) -> bool:
    prefix = "oauth_code_resource_"
    if not code.startswith(prefix):
        return resource is None
    if resource is None:
        return False
    encoded_fingerprint = code[len(prefix) : len(prefix) + 64]
    expected_fingerprint = hashlib.sha256(resource.encode("utf-8")).hexdigest()
    return secrets.compare_digest(encoded_fingerprint, expected_fingerprint)


def raw_refresh_token(resource: str | None) -> str:
    if resource is None:
        return raw_token("refresh")
    fingerprint = hashlib.sha256(resource.encode("utf-8")).hexdigest()
    return f"refresh_resource_{fingerprint}_{secrets.token_urlsafe(32)}"


def refresh_token_matches_resource(token: str, resource: str | None) -> bool:
    prefix = "refresh_resource_"
    if not token.startswith(prefix):
        return resource is None
    if resource is None:
        return False
    encoded_fingerprint = token[len(prefix) : len(prefix) + 64]
    expected_fingerprint = hashlib.sha256(resource.encode("utf-8")).hexdigest()
    return secrets.compare_digest(encoded_fingerprint, expected_fingerprint)


def hash_secret(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def expires_at(now: str, seconds: int) -> str:
    return (datetime.fromisoformat(now) + timedelta(seconds=seconds)).isoformat()


def is_expired(expires_at_value: str, now: str) -> bool:
    return datetime.fromisoformat(expires_at_value) <= datetime.fromisoformat(now)


def authorization_response(
    raw_code: str,
    redirect_uri: str,
    state: str | None,
    created_at: str,
    expires_at_value: str,
) -> RuntimeJsonObject:
    return {
        "code": raw_code,
        "redirectUri": redirect_uri,
        "redirectTo": redirect_with_code(redirect_uri, raw_code, state),
        "state": state,
        "createdAt": created_at,
        "expiresAt": expires_at_value,
    }


def redirect_with_code(redirect_uri: str, code: str, state: str | None) -> str:
    parsed = urlsplit(redirect_uri)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append(("code", code))
    if state is not None:
        query.append(("state", state))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
