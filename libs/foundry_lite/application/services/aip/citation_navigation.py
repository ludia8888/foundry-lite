"""Opaque, short-lived citation navigation token codec."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

_TOKEN_PREFIX = ("flite-citation-nav", "v1")
_MAX_TOKEN_BYTES = 32 * 1024
_MAX_TTL = timedelta(minutes=15)


@dataclass(frozen=True)
class CitationNavigationTokenError(Exception):
    """Safe token rejection with no source content."""

    reason: str
    detail: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.detail)


def citation_expiry(issued_at: str) -> str:
    """Return the canonical 15-minute expiry for an issued-at timestamp."""

    issued = _parse_timestamp(issued_at, "iat")
    return _timestamp(issued + _MAX_TTL)


def encode_navigation_ref(payload: Mapping[str, object], signing_key: str) -> str:
    """Sign an allowlisted citation payload after validating its bounded window."""

    _require_signing_key(signing_key)
    _validate_window(payload, observed_at=None)
    encoded = _base64url(_json_block(payload).encode("utf-8"))
    signature = _signature(encoded, signing_key)
    return ".".join((*_TOKEN_PREFIX, encoded, signature))


def decode_navigation_ref(
    navigation_ref: str,
    signing_key: str,
    *,
    expected_tenant_id: str,
    observed_at: datetime,
) -> dict[str, object]:
    """Verify signature first, then validate tenant and temporal bounds."""

    _require_signing_key(signing_key)
    parts = navigation_ref.split(".")
    if len(parts) != 4 or tuple(parts[:2]) != _TOKEN_PREFIX:
        raise CitationNavigationTokenError("invalid_navigation_ref", "citation navigation token is malformed")
    encoded, supplied_signature = parts[2], parts[3]
    expected_signature = _signature(encoded, signing_key)
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise CitationNavigationTokenError("invalid_navigation_signature", "citation navigation signature is invalid")
    payload = _decode_payload(encoded)
    if _required_string(payload, "tenant_id") != expected_tenant_id:
        raise CitationNavigationTokenError("navigation_tenant_mismatch", "citation navigation tenant does not match")
    _validate_window(payload, observed_at=observed_at)
    return payload


def _decode_payload(encoded: str) -> dict[str, object]:
    if not encoded or len(encoded) > _MAX_TOKEN_BYTES:
        raise CitationNavigationTokenError("invalid_navigation_ref", "citation navigation payload is invalid")
    try:
        padding = "=" * (-len(encoded) % 4)
        raw = base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
        decoded = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise CitationNavigationTokenError("invalid_navigation_ref", "citation navigation payload is invalid") from exc
    if not isinstance(decoded, Mapping):
        raise CitationNavigationTokenError("invalid_navigation_ref", "citation navigation payload is invalid")
    return dict(cast(Mapping[str, object], decoded))


def _validate_window(payload: Mapping[str, object], observed_at: datetime | None) -> None:
    issued = _parse_timestamp(_required_string(payload, "iat"), "iat")
    expires = _parse_timestamp(_required_string(payload, "exp"), "exp")
    if expires <= issued or expires - issued > _MAX_TTL:
        raise CitationNavigationTokenError(
            "invalid_navigation_window",
            "citation navigation lifetime must be positive and no longer than 15 minutes",
        )
    if observed_at is None:
        return
    observed = _aware_utc(observed_at)
    if observed < issued:
        raise CitationNavigationTokenError("navigation_not_yet_valid", "citation navigation token is not yet valid")
    if observed > expires:
        raise CitationNavigationTokenError("navigation_expired", "citation navigation token has expired")


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CitationNavigationTokenError(
            "invalid_navigation_window",
            f"citation navigation {field} timestamp is invalid",
        ) from exc
    if parsed.tzinfo is None:
        raise CitationNavigationTokenError(
            "invalid_navigation_window",
            f"citation navigation {field} timestamp requires a timezone",
        )
    return parsed.astimezone(UTC)


def _required_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise CitationNavigationTokenError("invalid_navigation_ref", f"citation navigation field {key} is missing")
    return value


def _require_signing_key(signing_key: str) -> None:
    if not signing_key:
        raise CitationNavigationTokenError(
            "navigation_signing_key_missing", "citation navigation signer is unavailable"
        )


def _signature(encoded: str, signing_key: str) -> str:
    return hmac.new(signing_key.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _json_block(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _timestamp(value: datetime) -> str:
    return _aware_utc(value).isoformat().replace("+00:00", "Z")


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise CitationNavigationTokenError(
            "invalid_navigation_window",
            "citation navigation observed timestamp requires a timezone",
        )
    return value.astimezone(UTC)
