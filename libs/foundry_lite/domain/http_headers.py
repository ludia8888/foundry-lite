"""Pure HTTP header safety rules for secret-backed connector authentication."""

from __future__ import annotations

import re
from typing import TypeGuard

_HEADER_NAME_PATTERN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+\Z")
_SYSTEM_OWNED_HEADERS = frozenset(
    {
        "authorization",
        "connection",
        "content-length",
        "content-type",
        "expect",
        "host",
        "idempotency-key",
        "keep-alive",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)


def is_safe_custom_header_name(value: object) -> TypeGuard[str]:
    """Accept an RFC token only when the connector runtime does not own it."""

    return (
        isinstance(value, str)
        and _HEADER_NAME_PATTERN.fullmatch(value) is not None
        and value.lower() not in _SYSTEM_OWNED_HEADERS
    )


def is_safe_http_header_value(value: object) -> TypeGuard[str]:
    """Reject request-splitting characters while preserving ordinary secret text."""

    return isinstance(value, str) and bool(value) and all(ord(char) >= 32 and ord(char) != 127 for char in value)


__all__ = ["is_safe_custom_header_name", "is_safe_http_header_value"]
