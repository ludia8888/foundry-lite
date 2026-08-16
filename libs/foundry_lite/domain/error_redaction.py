"""Pure error-text redaction shared by ports, services, APIs, and workers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import cast

_SECRET_KEY_PARTS = frozenset(
    {
        "authorization",
        "cookie",
        "setcookie",
        "token",
        "accesstoken",
        "refreshtoken",
        "apikey",
        "clientsecret",
        "connectionstring",
        "credentials",
        "databaseurl",
        "dsn",
        "headervalue",
        "hmac",
        "jwt",
        "privatekey",
        "secret",
        "signature",
        "password",
        "plaintext",
        "prompt",
        "compiledprompt",
        "providerrequest",
        "providerresponse",
    }
)
_SAFE_OBSERVABILITY_KEYS = frozenset(
    {
        "inputtokens",
        "outputtokens",
        "prompthash",
        "promptmode",
        "promptversionid",
        "totaltokens",
        "providerrequestid",
        "requestid",
        "stopreason",
    }
)
_TOKEN_COUNT_KEYS = frozenset({"inputtokens", "outputtokens", "totaltokens"})
_SAFE_STOP_REASON_VALUES = frozenset(
    {
        "endturn",
        "maxtokens",
        "modelcontextwindowexceeded",
        "pauseturn",
        "refusal",
        "stopsequence",
        "tooluse",
    }
)
_ALWAYS_SENSITIVE_TEXT_PARTS = frozenset(
    {
        "compiledprompt",
        "plaintext",
        "prompt",
    }
)
_AUTHORIZATION_PATTERN = re.compile(r"\bauthorization\s*:\s*bearer\s+\S+", re.IGNORECASE)
_BEARER_PATTERN = re.compile(r"\bbearer\s+\S+", re.IGNORECASE)
_URL_USERINFO_PATTERN = re.compile(
    r"(?P<scheme>\b[a-z][a-z0-9+.-]*://)(?P<userinfo>[^/\s?#]+@)(?=[^/\s?#]+(?:[/\s?#]|$))",
    re.IGNORECASE,
)
_ASSIGNMENT_PATTERN = re.compile(
    r"\b(authorization|token|access[_-]?token|refresh[_-]?token|api[_-]?key|client[_-]?secret|"
    r"connection[_-]?string|credentials?|database[_-]?url|dsn|header[_-]?value|hmac|jwt|"
    r"private[_-]?key|secret|signature|password|plain[_-]?text|prompt|compiled[_-]?prompt|"
    r"provider[_-]?request|provider[_-]?response)"
    r"\s*[:=]\s*\S+",
    re.IGNORECASE,
)
_SAFE_HASH_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SAFE_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9._:@/-]{1,256}\Z")


def scrub_error_mapping(value: Mapping[str, object]) -> dict[str, object]:
    """Recursively mask secret keys and secret-bearing text in an error payload."""

    return cast(dict[str, object], _scrub_error_payload(value))


def scrub_error_text(value: str) -> str:
    """Mask credentials and sensitive request/model text embedded in an error string."""

    return _scrub_text(value)


def _scrub_error_payload(value: object) -> object:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return {str(key): _scrub_mapping_item(key, item) for key, item in mapping.items()}
    if isinstance(value, list):
        return [_scrub_error_payload(item) for item in cast(list[object], value)]
    if isinstance(value, tuple):
        return tuple(_scrub_error_payload(item) for item in cast(tuple[object, ...], value))
    if isinstance(value, str):
        return _scrub_text(value)
    return value


def _scrub_mapping_item(key: object, value: object) -> object:
    if _is_redaction_marker(value):
        return value
    if _is_secret_key(key):
        return "***MASKED***"
    if _is_safe_observability_key(key):
        if _is_safe_observability_value(key, value):
            return value
        if isinstance(value, str) and _URL_USERINFO_PATTERN.search(value) is not None:
            return _scrub_text(value)
        return "***MASKED***"
    return _scrub_error_payload(value)


def _scrub_text(value: str) -> str:
    if _is_redaction_marker(value):
        return value
    if _normalize_secret_text(value) in _SAFE_STOP_REASON_VALUES:
        return value
    normalized = _normalize_secret_text(value)
    if any(term in normalized for term in _ALWAYS_SENSITIVE_TEXT_PARTS) or (
        _AUTHORIZATION_PATTERN.search(value) is not None
        or _BEARER_PATTERN.search(value) is not None
        or _ASSIGNMENT_PATTERN.search(value) is not None
    ):
        return "***MASKED***"
    scrubbed = _URL_USERINFO_PATTERN.sub(lambda match: f"{match.group('scheme')}***MASKED***@", value)
    return scrubbed


def _is_redaction_marker(value: object) -> bool:
    return isinstance(value, str) and value in {"***MASKED***", "***REDACTED***"}


def _is_secret_key(key: object) -> bool:
    normalized = _normalize_secret_text(str(key))
    if normalized in _SAFE_OBSERVABILITY_KEYS:
        return False
    return any(term in normalized for term in _SECRET_KEY_PARTS)


def _is_safe_observability_key(key: object) -> bool:
    return _normalize_secret_text(str(key)) in _SAFE_OBSERVABILITY_KEYS


def _is_safe_observability_value(key: object, value: object) -> bool:
    normalized = _normalize_secret_text(str(key))
    if normalized in _TOKEN_COUNT_KEYS:
        return isinstance(value, int) and not isinstance(value, bool)
    if not isinstance(value, str):
        return False
    if _URL_USERINFO_PATTERN.search(value) is not None:
        return False
    if normalized == "prompthash":
        return _SAFE_HASH_PATTERN.fullmatch(value) is not None
    if normalized == "promptmode":
        return value in {"text", "basic_vision", "layout_aware_vision"}
    if normalized == "stopreason":
        return _normalize_secret_text(value) in _SAFE_STOP_REASON_VALUES
    return _SAFE_IDENTIFIER_PATTERN.fullmatch(value) is not None


def _normalize_secret_text(value: str) -> str:
    return "".join(char for char in value.casefold() if char.isalnum())


__all__ = ["scrub_error_mapping", "scrub_error_text"]
