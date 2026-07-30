"""Runtime-profile policy for the connector/source private-network SSRF bypass.

``allow_private_network`` turns off the REST connector SSRF guard so a request can
reach loopback, link-local, private, and cloud-metadata hosts. That is only a
local-harness convenience: in a protected runtime profile (production/staging)
real internal infrastructure exists, so honoring the flag there would let a
caller pivot to internal services. This module centralizes the "protected runtime
profile" check used to (a) refuse setting the flag from a client request and
(b) neutralize any stored flag before it reaches the network.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from foundry_lite.domain.errors import PermissionDenied

RUNTIME_PROFILE_ENV = "FOUNDRY_LITE_RUNTIME_PROFILE"
PROTECTED_RUNTIME_PROFILES = frozenset({"production", "prod", "staging", "stage"})


def runtime_profile_name(environ: Mapping[str, str] | None = None) -> str:
    source = environ if environ is not None else os.environ
    return source.get(RUNTIME_PROFILE_ENV, "local").strip().casefold().replace("_", "-")


def is_protected_runtime(environ: Mapping[str, str] | None = None) -> bool:
    return runtime_profile_name(environ) in PROTECTED_RUNTIME_PROFILES


def private_network_bypass_permitted(allow_private_network: bool, environ: Mapping[str, str] | None = None) -> bool:
    """Return the effective bypass value, forced off in protected runtime profiles."""
    return bool(allow_private_network) and not is_protected_runtime(environ)


def require_private_network_bypass_allowed(allow_private_network: bool | None, *, resource: str) -> None:
    """Reject a client request that enables the private-network bypass in production.

    Only meaningful when the caller is explicitly enabling the flag: ``None``
    (leave unchanged) and ``False`` are always allowed. In a protected runtime
    profile an explicit ``True`` is refused so the SSRF bypass can never be stored
    or acted on there — no routine role can flip an unconditional kill-switch.
    """
    if not allow_private_network:
        return
    profile = runtime_profile_name()
    if profile not in PROTECTED_RUNTIME_PROFILES:
        return
    raise PermissionDenied(
        "private network access is disabled for protected runtime profiles",
        details={
            "runtime_profile": profile,
            "resource": resource,
            "capability": "allowPrivateNetwork",
        },
    )
