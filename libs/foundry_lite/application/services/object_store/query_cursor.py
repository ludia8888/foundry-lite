"""Opaque cursor encoding for object query keyset pagination."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import ObjectOrderBy, ObjectQueryCursor, ObjectRecordRow
from foundry_lite.application.primitives import _json_ready
from foundry_lite.domain.errors import ValidationFailed

CURSOR_PREFIX = "oqc2."
CURSOR_SIGNING_KEY_ENV = "FOUNDRY_LITE_OBJECT_QUERY_CURSOR_SIGNING_KEY"
CURSOR_SIGNING_KEY_ID_ENV = "FOUNDRY_LITE_OBJECT_QUERY_CURSOR_SIGNING_KEY_ID"
CURSOR_SIGNING_KEYS_JSON_ENV = "FOUNDRY_LITE_OBJECT_QUERY_CURSOR_SIGNING_KEYS_JSON"
CURSOR_TTL_SECONDS_ENV = "FOUNDRY_LITE_OBJECT_QUERY_CURSOR_TTL_SECONDS"
DEFAULT_CURSOR_SIGNING_KEY = "foundry-lite-object-query-cursor-v1"
DEFAULT_CURSOR_SIGNING_KEY_ID = "local-default"
DEFAULT_CURSOR_TTL_SECONDS = 900
RUNTIME_PROFILE_ENV = "FOUNDRY_LITE_RUNTIME_PROFILE"
PROTECTED_RUNTIME_PROFILES = frozenset({"production", "prod", "staging", "stage"})


def encode_object_query_cursor(
    row: ObjectRecordRow,
    order_by: Sequence[ObjectOrderBy],
    filter_ast: Mapping[str, object] | None,
    active_index_version: str,
    *,
    actor_user_id: str,
    tenant_id: str,
    now_epoch: int | None = None,
) -> str:
    """Encode the next-page cursor and bind it to caller/query scope."""
    issued_at = _now_epoch(now_epoch)
    payload = _signed_payload(
        {
            "activeIndexVersion": active_index_version,
            "actorUserId": actor_user_id,
            "expiresAt": issued_at + _cursor_ttl_seconds(os.environ),
            "kid": _current_key_id(os.environ),
            "order": _order_signature(order_by),
            "shape": _shape_checksum(order_by, filter_ast),
            "tenantId": tenant_id,
            "values": [_json_ready(row["properties"].get(order["property"])) for order in order_by],
            "objectId": row["object_id"],
        }
    )
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return CURSOR_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_object_query_cursor(
    cursor: str | None,
    order_by: Sequence[ObjectOrderBy],
    filter_ast: Mapping[str, object] | None,
    active_index_version: str,
    *,
    actor_user_id: str,
    tenant_id: str,
    now_epoch: int | None = None,
) -> ObjectQueryCursor | None:
    """Decode and validate an object query cursor for the active request."""
    if cursor is None:
        return None
    if not cursor.startswith(CURSOR_PREFIX):
        raise ValidationFailed("invalid object query cursor")
    payload = _cursor_payload(cursor)
    _require_cursor_scope(payload, actor_user_id=actor_user_id, tenant_id=tenant_id, now_epoch=now_epoch)
    if payload.get("activeIndexVersion") != active_index_version:
        raise ValidationFailed(
            "object query cursor active index version changed",
            details={
                "cursorActiveIndexVersion": payload.get("activeIndexVersion"),
                "currentActiveIndexVersion": active_index_version,
            },
        )
    if payload.get("order") != _order_signature(order_by):
        raise ValidationFailed("object query cursor does not match orderBy")
    if payload.get("shape") != _shape_checksum(order_by, filter_ast):
        raise ValidationFailed("object query cursor does not match query shape")
    values = payload.get("values")
    object_id = payload.get("objectId")
    if not isinstance(values, list) or not isinstance(object_id, str):
        raise ValidationFailed("invalid object query cursor")
    if len(values) != len(order_by):
        raise ValidationFailed("object query cursor does not match orderBy")
    return {"values": values, "object_id": object_id, "active_index_version": active_index_version}


def _cursor_payload(cursor: str) -> dict[str, object]:
    """Decode the raw token payload and verify its signature."""
    encoded = cursor.removeprefix(CURSOR_PREFIX)
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationFailed("invalid object query cursor") from exc
    if not isinstance(payload, dict):
        raise ValidationFailed("invalid object query cursor")
    normalized = {str(key): value for key, value in payload.items()}
    _require_valid_signature(normalized)
    return normalized


def _signed_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Return payload with a signature over its unsigned fields."""
    signed = dict(payload)
    signed["signature"] = _payload_signature(signed)
    return signed


def _require_valid_signature(payload: Mapping[str, object]) -> None:
    """Reject cursors whose embedded signature does not match."""
    provided = payload.get("signature")
    if not isinstance(provided, str):
        raise ValidationFailed("invalid object query cursor")
    expected = _payload_signature(payload)
    if not hmac.compare_digest(provided, expected):
        raise ValidationFailed("invalid object query cursor")


def _payload_signature(payload: Mapping[str, object]) -> str:
    """Build the HMAC signature for the payload key id."""
    raw = _canonical_payload(_unsigned_payload(payload)).encode("utf-8")
    kid = payload.get("kid")
    if not isinstance(kid, str):
        raise ValidationFailed("invalid object query cursor")
    signing_key = _cursor_signing_key(os.environ, kid)
    return hmac.new(signing_key, raw, hashlib.sha256).hexdigest()[:32]


def require_object_query_cursor_signing_key_for_runtime(environ: Mapping[str, str] | None = None) -> None:
    """Require explicit cursor key material in protected runtime profiles."""
    env = os.environ if environ is None else environ
    profile = str(env.get(RUNTIME_PROFILE_ENV, "local")).casefold()
    if profile not in PROTECTED_RUNTIME_PROFILES:
        return
    if not env.get(CURSOR_SIGNING_KEY_ID_ENV):
        raise ValidationFailed(
            "object query cursor signing key id is required for protected runtime profiles",
            details={
                "env_var": CURSOR_SIGNING_KEY_ID_ENV,
                "runtime_profile": profile,
            },
        )
    if not _has_current_signing_key(env):
        raise ValidationFailed(
            "object query cursor signing key is required for protected runtime profiles",
            details={
                "env_var": CURSOR_SIGNING_KEY_ENV,
                "runtime_profile": profile,
            },
        )


def _cursor_signing_key(environ: Mapping[str, str], kid: str) -> bytes:
    """Resolve a signing key by key id."""
    require_object_query_cursor_signing_key_for_runtime(environ)
    signing_key = _signing_keys(environ).get(kid)
    if not signing_key:
        raise ValidationFailed("invalid object query cursor")
    return signing_key.encode("utf-8")


def _require_cursor_scope(
    payload: Mapping[str, object],
    *,
    actor_user_id: str,
    tenant_id: str,
    now_epoch: int | None,
) -> None:
    """Reject cursor reuse outside the original caller scope or TTL."""
    if payload.get("tenantId") != tenant_id:
        raise ValidationFailed("object query cursor does not match tenant")
    if payload.get("actorUserId") != actor_user_id:
        raise ValidationFailed("object query cursor does not match user")
    expires_at = payload.get("expiresAt")
    if not isinstance(expires_at, int) or isinstance(expires_at, bool):
        raise ValidationFailed("invalid object query cursor")
    if _now_epoch(now_epoch) > expires_at:
        raise ValidationFailed(
            "object query cursor expired",
            details={"expiresAt": expires_at},
        )


def _has_current_signing_key(environ: Mapping[str, str]) -> bool:
    """Return whether the configured current key id has key material."""
    return bool(_signing_keys(environ).get(_current_key_id(environ)))


def _signing_keys(environ: Mapping[str, str]) -> dict[str, str]:
    """Return all key ids accepted for cursor verification."""
    keys = _json_signing_keys(environ)
    single_key = environ.get(CURSOR_SIGNING_KEY_ENV)
    if single_key:
        keys[_current_key_id(environ)] = single_key
    if not keys and _runtime_profile(environ) not in PROTECTED_RUNTIME_PROFILES:
        keys[DEFAULT_CURSOR_SIGNING_KEY_ID] = DEFAULT_CURSOR_SIGNING_KEY
    return keys


def _json_signing_keys(environ: Mapping[str, str]) -> dict[str, str]:
    """Parse the optional key-ring JSON environment variable."""
    raw = environ.get(CURSOR_SIGNING_KEYS_JSON_ENV)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise ValidationFailed("invalid object query cursor key configuration") from exc
    if not isinstance(parsed, dict):
        raise ValidationFailed("invalid object query cursor key configuration")
    return _string_key_map(parsed)


def _string_key_map(parsed: Mapping[object, object]) -> dict[str, str]:
    """Validate that the parsed key ring is a string-to-string map."""
    keys = {str(key): value for key, value in parsed.items() if isinstance(value, str) and str(key)}
    if len(keys) != len(parsed):
        raise ValidationFailed("invalid object query cursor key configuration")
    return keys


def _current_key_id(environ: Mapping[str, str]) -> str:
    """Return the key id used for new cursor signatures."""
    key_id = environ.get(CURSOR_SIGNING_KEY_ID_ENV, DEFAULT_CURSOR_SIGNING_KEY_ID).strip()
    if not key_id:
        raise ValidationFailed("invalid object query cursor key configuration")
    return key_id


def _cursor_ttl_seconds(environ: Mapping[str, str]) -> int:
    """Return the bounded cursor TTL in seconds."""
    raw = environ.get(CURSOR_TTL_SECONDS_ENV, str(DEFAULT_CURSOR_TTL_SECONDS))
    try:
        ttl_seconds = int(raw)
    except ValueError as exc:
        raise ValidationFailed("invalid object query cursor ttl") from exc
    if ttl_seconds < 1 or ttl_seconds > 86_400:
        raise ValidationFailed("invalid object query cursor ttl")
    return ttl_seconds


def _runtime_profile(environ: Mapping[str, str]) -> str:
    """Return the normalized runtime profile name."""
    return str(environ.get(RUNTIME_PROFILE_ENV, "local")).casefold()


def _now_epoch(now_epoch: int | None = None) -> int:
    """Return epoch seconds, allowing deterministic test injection."""
    return int(time.time()) if now_epoch is None else now_epoch


def _unsigned_payload(payload: Mapping[str, object]) -> dict[str, object]:
    """Drop the signature field before canonical signing."""
    return {key: value for key, value in payload.items() if key != "signature"}


def _canonical_payload(payload: Mapping[str, object]) -> str:
    """Serialize payload into the stable JSON form used by HMAC."""
    return json.dumps(_json_ready(payload), sort_keys=True, separators=(",", ":"))


def _order_signature(order_by: Sequence[ObjectOrderBy]) -> list[str]:
    """Return the compact orderBy signature stored in the cursor."""
    return [f"{order['property']}:{order['direction']}" for order in order_by]


def _shape_checksum(order_by: Sequence[ObjectOrderBy], filter_ast: Mapping[str, object] | None) -> str:
    """Return the checksum binding a cursor to filter and ordering shape."""
    payload = json.dumps(
        {"filter": _json_ready(filter_ast or {}), "order": _order_signature(order_by)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
