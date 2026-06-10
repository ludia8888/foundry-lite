"""Local infrastructure adapters for the AuthProvider port."""

from foundry_lite.infrastructure.auth.local import (
    HEADER_ROLES_KEY,
    HEADER_TENANT_KEY,
    HEADER_USER_KEY,
    DemoAuthProvider,
    HeaderTrustAuthProvider,
)

__all__ = [
    "HEADER_ROLES_KEY",
    "HEADER_TENANT_KEY",
    "HEADER_USER_KEY",
    "DemoAuthProvider",
    "HeaderTrustAuthProvider",
]
