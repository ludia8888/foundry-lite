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
        "plaintext",
        "prompt",
        "compiledprompt",
        "providerrequest",
        "providerresponse",
        "actionproposal",
    }
)


def redact_sensitive(value: object, sensitive: set[str]) -> object:
    normalized_sensitive = {_normalize_json_key(key) for key in sensitive}
    return _redact_sensitive_value(value, sensitive, normalized_sensitive)


def _redact_sensitive_value(value: object, sensitive: set[str], normalized_sensitive: set[str]) -> object:
    if isinstance(value, Mapping):
        return {
            key: "***MASKED***"
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
    return any(term in normalized for term in OPERATIONS_FORBIDDEN_JSON_KEY_PARTS)


def _normalize_json_key(value: object) -> str:
    return "".join(char for char in str(value).casefold() if char.isalnum())
