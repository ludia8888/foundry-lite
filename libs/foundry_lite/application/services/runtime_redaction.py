"""Recursive redaction helpers for runtime evidence payloads."""

from __future__ import annotations

from collections.abc import Mapping

OPERATIONS_FORBIDDEN_JSON_KEY_PARTS = frozenset(
    {
        "authorization",
        "cookie",
        "setcookie",
        "token",
        "accesstoken",
        "refreshtoken",
        "apikey",
        "secret",
        "password",
    }
)
OPERATIONS_FORBIDDEN_JSON_KEYS = frozenset(
    {
        "actionproposal",
        "compiledprompt",
        "plaintext",
        "prompt",
        "providerrequest",
        "providerresponse",
    }
)
OPERATIONS_TOKEN_COUNT_KEYS = frozenset(
    {
        "inputtokens",
        "outputtokens",
        "totaltokens",
    }
)


def redact_sensitive(value: object, sensitive: set[str]) -> object:
    normalized_sensitive = {_normalize_json_key(key) for key in sensitive}
    return _redact_sensitive_value(value, sensitive, normalized_sensitive)


def _redact_sensitive_value(value: object, sensitive: set[str], normalized_sensitive: set[str]) -> object:
    if isinstance(value, Mapping):
        return {
            key: _redacted_value(key, item)
            if _should_redact_json_key(key, sensitive, normalized_sensitive)
            else (_redact_sensitive_value(item, sensitive, normalized_sensitive))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive_value(item, sensitive, normalized_sensitive) for item in value]
    return value


def _should_redact_json_key(key: object, sensitive: set[str], normalized_sensitive: set[str]) -> bool:
    if key in sensitive:
        return True
    normalized = _normalize_json_key(key)
    if normalized in normalized_sensitive:
        return True
    if normalized in OPERATIONS_TOKEN_COUNT_KEYS:
        return False
    if normalized in OPERATIONS_FORBIDDEN_JSON_KEYS:
        return True
    return any(term in normalized for term in OPERATIONS_FORBIDDEN_JSON_KEY_PARTS)


def _redacted_value(key: object, value: object) -> str:
    if value == "***REDACTED***":
        return "***REDACTED***"
    normalized = _normalize_json_key(key)
    if any(term in normalized for term in ("apikey", "password", "secret", "token")):
        return "***REDACTED***"
    return "***MASKED***"


def _normalize_json_key(value: object) -> str:
    return "".join(char for char in str(value).casefold() if char.isalnum())
