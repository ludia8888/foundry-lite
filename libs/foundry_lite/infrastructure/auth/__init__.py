"""Local infrastructure adapters for the AuthProvider port."""

from foundry_lite.infrastructure.auth.local import (
    HEADER_ROLES_KEY,
    HEADER_TENANT_KEY,
    HEADER_USER_KEY,
    DemoAuthProvider,
    HeaderTrustAuthProvider,
)
from foundry_lite.infrastructure.auth.oidc import (
    AUTHORIZATION_HEADER,
    OIDC_AUDIENCE_ENV,
    OIDC_DISCOVERY_JSON_ENV,
    OIDC_ISSUER_ENV,
    OIDC_JWKS_JSON_ENV,
    OIDC_REVOKED_JTIS_JSON_ENV,
    OIDC_ROLES_CLAIM_ENV,
    OIDC_SERVICE_ACCOUNT_CLAIM_ENV,
    OIDC_TENANT_CLAIM_ENV,
    JwtOidcAuthConfig,
    JwtOidcAuthProvider,
)
from foundry_lite.infrastructure.auth.profiles import (
    AUTH_PROFILE_ENV,
    DEFAULT_AUTH_PROFILE,
    DEFAULT_RUNTIME_PROFILE,
    RUNTIME_PROFILE_ENV,
    AuthProfileConfigurationError,
    auth_provider_for_profile,
    auth_provider_from_env,
)

__all__ = [
    "AUTH_PROFILE_ENV",
    "AUTHORIZATION_HEADER",
    "DEFAULT_AUTH_PROFILE",
    "DEFAULT_RUNTIME_PROFILE",
    "HEADER_ROLES_KEY",
    "HEADER_TENANT_KEY",
    "HEADER_USER_KEY",
    "RUNTIME_PROFILE_ENV",
    "AuthProfileConfigurationError",
    "DemoAuthProvider",
    "HeaderTrustAuthProvider",
    "JwtOidcAuthConfig",
    "JwtOidcAuthProvider",
    "OIDC_AUDIENCE_ENV",
    "OIDC_DISCOVERY_JSON_ENV",
    "OIDC_ISSUER_ENV",
    "OIDC_JWKS_JSON_ENV",
    "OIDC_REVOKED_JTIS_JSON_ENV",
    "OIDC_ROLES_CLAIM_ENV",
    "OIDC_SERVICE_ACCOUNT_CLAIM_ENV",
    "OIDC_TENANT_CLAIM_ENV",
    "auth_provider_for_profile",
    "auth_provider_from_env",
]
