"""Infrastructure authentication adapter for oauth token issuer."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from foundry_lite.application.ports import RuntimeJsonObject
from foundry_lite.application.ports.oauth_session_repository import OAuthAccessTokenClaims, OAuthIssuedAccessToken

_DEFAULT_ISSUER = "https://foundry-lite.local/osdk-oauth"
_DEFAULT_AUDIENCE = "foundry-lite-osdk"
_DEFAULT_KEY_ID = "foundry-lite-local-osdk-oauth"
OAUTH_ISSUER_ENV = "FOUNDRY_LITE_OAUTH_ISSUER"
OAUTH_AUDIENCE_ENV = "FOUNDRY_LITE_OAUTH_AUDIENCE"
OAUTH_PRIVATE_KEY_PATH_ENV = "FOUNDRY_LITE_OAUTH_PRIVATE_KEY_PATH"


@dataclass(frozen=True)
class LocalOAuthTokenIssuer:
    """Local RS256 OAuth access-token issuer for OSDK app sessions."""

    private_key: rsa.RSAPrivateKey
    issuer: str = _DEFAULT_ISSUER
    audience: str = _DEFAULT_AUDIENCE
    key_id: str = _DEFAULT_KEY_ID
    retired_public_jwks: tuple[RuntimeJsonObject, ...] = ()

    @classmethod
    def from_key_path(
        cls,
        path: Path,
        *,
        issuer: str | None = None,
        audience: str | None = None,
    ) -> LocalOAuthTokenIssuer:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            return cls(
                _load_private_key(path),
                issuer=issuer or _DEFAULT_ISSUER,
                audience=audience or _DEFAULT_AUDIENCE,
            )
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        path.write_bytes(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        return cls(key, issuer=issuer or _DEFAULT_ISSUER, audience=audience or _DEFAULT_AUDIENCE)

    def issue_access_token(self, claims: OAuthAccessTokenClaims, *, ttl_seconds: int) -> OAuthIssuedAccessToken:
        now = int(time.time())
        expires_at = now + ttl_seconds
        payload = {
            "iss": self.issuer,
            "aud": claims.get("resource") or self.audience,
            "iat": now,
            "exp": expires_at,
            "tenant_id": claims["tenant_id"],
            "sub": claims["actor_user_id"],
            "roles": claims["roles"],
            "osdk_app_id": claims["application_id"],
            "client_id": claims["client_id"],
            "scope": " ".join(claims["scopes"]),
            "sid": claims["session_id"],
            "foundry_lite_session_id": claims["session_id"],
            "gty": "authorization_code",
            "jti": f"osdk_access_{uuid.uuid4().hex}",
        }
        token = jwt.encode(payload, self.private_key, algorithm="RS256", headers={"kid": self.key_id})
        return {
            "accessToken": token,
            "tokenType": "Bearer",
            "expiresIn": ttl_seconds,
            "claims": cast(RuntimeJsonObject, {key: value for key, value in payload.items() if key != "exp"}),
        }

    def public_jwks(self) -> RuntimeJsonObject:
        return {"keys": [_public_jwk(self.private_key, self.key_id), *self.retired_public_jwks]}

    def rotated(self, private_key: rsa.RSAPrivateKey, *, key_id: str) -> LocalOAuthTokenIssuer:
        return LocalOAuthTokenIssuer(
            private_key=private_key,
            issuer=self.issuer,
            audience=self.audience,
            key_id=key_id,
            retired_public_jwks=(*self.retired_public_jwks, _public_jwk(self.private_key, self.key_id)),
        )


def _public_jwk(private_key: rsa.RSAPrivateKey, key_id: str) -> RuntimeJsonObject:
    jwk = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    jwk["kid"] = key_id
    jwk["alg"] = "RS256"
    jwk["use"] = "sig"
    return cast(RuntimeJsonObject, jwk)


def _load_private_key(path: Path) -> rsa.RSAPrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("OSDK OAuth private key must be an RSA key")
    return key
