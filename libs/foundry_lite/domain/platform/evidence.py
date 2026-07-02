"""Pure evidence redaction rules."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

FORBIDDEN_JSON_KEY_PARTS = frozenset(
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
        "databaseurl",
        "dsn",
        "headervalue",
        "hmac",
        "jwt",
        "privatekey",
        "secret",
        "signature",
        "password",
    }
)
FORBIDDEN_JSON_KEYS = frozenset(
    {
        "actionproposal",
        "compiledprompt",
        "credentials",
        "plaintext",
        "prompt",
        "providerrequest",
        "providerresponse",
    }
)
ALLOWED_STATUS_KEYS = frozenset({"authorizationdecision"})
TOKEN_COUNT_KEYS = frozenset({"inputtokens", "outputtokens", "totaltokens"})
SECRET_VALUE_PARTS = frozenset(
    {
        "apikey",
        "clientsecret",
        "connectionstring",
        "databaseurl",
        "dsn",
        "headervalue",
        "hmac",
        "jwt",
        "password",
        "privatekey",
        "secret",
        "signature",
        "token",
    }
)


def redact_evidence(value: object, sensitive_keys: set[str] | None = None) -> object:
    normalized_sensitive = {_normalize_json_key(key) for key in sensitive_keys or set()}
    return _redact_sensitive_value(value, sensitive_keys or set(), normalized_sensitive)


def _redact_sensitive_value(value: object, sensitive: set[str], normalized_sensitive: set[str]) -> object:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return {
            key: _redacted_value(key, item)
            if _should_redact_json_key(key, sensitive, normalized_sensitive)
            else _redact_sensitive_value(item, sensitive, normalized_sensitive)
            for key, item in mapping.items()
        }
    if isinstance(value, list):
        list_items = cast(list[object], value)
        return [_redact_sensitive_value(item, sensitive, normalized_sensitive) for item in list_items]
    if isinstance(value, tuple):
        tuple_items = cast(tuple[object, ...], value)
        return tuple(_redact_sensitive_value(item, sensitive, normalized_sensitive) for item in tuple_items)
    return value


def _should_redact_json_key(key: object, sensitive: set[str], normalized_sensitive: set[str]) -> bool:
    if key in sensitive:
        return True
    normalized = _normalize_json_key(key)
    if normalized in normalized_sensitive:
        return True
    if normalized in TOKEN_COUNT_KEYS or normalized in ALLOWED_STATUS_KEYS:
        return False
    if normalized in FORBIDDEN_JSON_KEYS:
        return True
    return any(term in normalized for term in FORBIDDEN_JSON_KEY_PARTS)


def _redacted_value(key: object, value: object) -> str:
    if value == "***REDACTED***":
        return "***REDACTED***"
    normalized = _normalize_json_key(key)
    if any(term in normalized for term in SECRET_VALUE_PARTS):
        return "***REDACTED***"
    return "***MASKED***"


def _normalize_json_key(value: object) -> str:
    return "".join(char for char in str(value).casefold() if char.isalnum())
