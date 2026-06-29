from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from collections.abc import Mapping

from foundry_lite.application.ports import RuntimeRow, RuntimeRunPageCursor, RuntimeRunType
from foundry_lite.domain.errors import ValidationFailed

OPERATIONS_CURSOR_PREFIX = "orc2."
OPERATIONS_CURSOR_SIGNING_KEY_ENV = "FOUNDRY_LITE_OPERATIONS_CURSOR_SIGNING_KEY"
OPERATIONS_CURSOR_SIGNING_KEY_ID_ENV = "FOUNDRY_LITE_OPERATIONS_CURSOR_SIGNING_KEY_ID"
OPERATIONS_CURSOR_SIGNING_KEYS_JSON_ENV = "FOUNDRY_LITE_OPERATIONS_CURSOR_SIGNING_KEYS_JSON"
OPERATIONS_CURSOR_TTL_SECONDS_ENV = "FOUNDRY_LITE_OPERATIONS_CURSOR_TTL_SECONDS"
DEFAULT_OPERATIONS_CURSOR_SIGNING_KEY = "foundry-lite-operations-cursor-v1"
DEFAULT_OPERATIONS_CURSOR_SIGNING_KEY_ID = "local-default"
DEFAULT_OPERATIONS_CURSOR_TTL_SECONDS = 900
RUNTIME_PROFILE_ENV = "FOUNDRY_LITE_RUNTIME_PROFILE"
PROTECTED_RUNTIME_PROFILES = frozenset({"production", "prod", "staging", "stage"})


def encode_runtime_run_cursor(
    row: RuntimeRow,
    *,
    actor_user_id: str,
    run_type: RuntimeRunType,
    status: str | None,
    since: str | None,
    tenant_id: str,
    until: str | None,
    now_epoch: int | None = None,
) -> str:
    issued_at = _now_epoch(now_epoch)
    payload = _signed_payload(
        {
            "actorUserId": actor_user_id,
            "expiresAt": issued_at + _cursor_ttl_seconds(os.environ),
            "kid": _current_key_id(os.environ),
            "runType": run_type,
            "shape": _shape_checksum(run_type=run_type, status=status, since=since, until=until),
            "tenantId": tenant_id,
            "timestamp": _row_timestamp(row),
            "runId": row["id"],
        }
    )
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{OPERATIONS_CURSOR_PREFIX}{base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')}"


def decode_runtime_run_cursor(
    cursor: str | None,
    *,
    actor_user_id: str,
    run_type: RuntimeRunType,
    status: str | None,
    since: str | None,
    tenant_id: str,
    until: str | None,
    now_epoch: int | None = None,
) -> RuntimeRunPageCursor | None:
    if cursor is None or cursor == "":
        return None
    if not cursor.startswith(OPERATIONS_CURSOR_PREFIX):
        raise ValidationFailed("invalid operations cursor")
    payload = _cursor_payload(cursor)
    _require_cursor_scope(payload, actor_user_id=actor_user_id, tenant_id=tenant_id, now_epoch=now_epoch)
    if payload.get("runType") != run_type:
        raise ValidationFailed("operations cursor does not match run type")
    expected_shape = _shape_checksum(run_type=run_type, status=status, since=since, until=until)
    if payload.get("shape") != expected_shape:
        raise ValidationFailed("operations cursor does not match query shape")
    timestamp = payload.get("timestamp")
    run_id = payload.get("runId")
    if not isinstance(timestamp, str) or not isinstance(run_id, str) or not timestamp or not run_id:
        raise ValidationFailed("invalid operations cursor payload")
    return {"timestamp": timestamp, "run_id": run_id}


def require_operations_cursor_signing_key_for_runtime(environ: Mapping[str, str] | None = None) -> None:
    """Require explicit Operations cursor key material in protected runtime profiles."""
    env = os.environ if environ is None else environ
    profile = _runtime_profile(env)
    if profile not in PROTECTED_RUNTIME_PROFILES:
        return
    if not env.get(OPERATIONS_CURSOR_SIGNING_KEY_ID_ENV):
        raise ValidationFailed(
            "operations cursor signing key id is required for protected runtime profiles",
            details={"env_var": OPERATIONS_CURSOR_SIGNING_KEY_ID_ENV, "runtime_profile": profile},
        )
    if not _has_current_signing_key(env):
        raise ValidationFailed(
            "operations cursor signing key is required for protected runtime profiles",
            details={"env_var": OPERATIONS_CURSOR_SIGNING_KEY_ENV, "runtime_profile": profile},
        )


def _signed_payload(payload: Mapping[str, object]) -> dict[str, object]:
    signed = dict(payload)
    signed["signature"] = _payload_signature(signed)
    return signed


def _cursor_payload(cursor: str) -> Mapping[str, object]:
    encoded = cursor.removeprefix(OPERATIONS_CURSOR_PREFIX)
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationFailed("invalid operations cursor") from exc
    if not isinstance(payload, dict):
        raise ValidationFailed("invalid operations cursor payload")
    normalized = {str(key): value for key, value in payload.items()}
    _require_valid_signature(normalized)
    return normalized


def _require_valid_signature(payload: Mapping[str, object]) -> None:
    provided = payload.get("signature")
    if not isinstance(provided, str):
        raise ValidationFailed("invalid operations cursor")
    expected = _payload_signature(payload)
    if not hmac.compare_digest(provided, expected):
        raise ValidationFailed("invalid operations cursor")


def _payload_signature(payload: Mapping[str, object]) -> str:
    raw = _canonical_payload(_unsigned_payload(payload)).encode("utf-8")
    kid = payload.get("kid")
    if not isinstance(kid, str):
        raise ValidationFailed("invalid operations cursor")
    signing_key = _cursor_signing_key(os.environ, kid)
    return hmac.new(signing_key, raw, hashlib.sha256).hexdigest()[:32]


def _cursor_signing_key(environ: Mapping[str, str], kid: str) -> bytes:
    require_operations_cursor_signing_key_for_runtime(environ)
    signing_key = _signing_keys(environ).get(kid)
    if not signing_key:
        raise ValidationFailed("invalid operations cursor")
    return signing_key.encode("utf-8")


def _require_cursor_scope(
    payload: Mapping[str, object],
    *,
    actor_user_id: str,
    tenant_id: str,
    now_epoch: int | None,
) -> None:
    if payload.get("tenantId") != tenant_id:
        raise ValidationFailed("operations cursor does not match tenant")
    if payload.get("actorUserId") != actor_user_id:
        raise ValidationFailed("operations cursor does not match user")
    expires_at = payload.get("expiresAt")
    if not isinstance(expires_at, int) or isinstance(expires_at, bool):
        raise ValidationFailed("invalid operations cursor payload")
    if _now_epoch(now_epoch) > expires_at:
        raise ValidationFailed("operations cursor expired", details={"expiresAt": expires_at})


def _has_current_signing_key(environ: Mapping[str, str]) -> bool:
    return bool(_signing_keys(environ).get(_current_key_id(environ)))


def _signing_keys(environ: Mapping[str, str]) -> dict[str, str]:
    keys = _json_signing_keys(environ)
    single_key = environ.get(OPERATIONS_CURSOR_SIGNING_KEY_ENV)
    if single_key:
        keys[_current_key_id(environ)] = single_key
    if not keys and _runtime_profile(environ) not in PROTECTED_RUNTIME_PROFILES:
        keys[DEFAULT_OPERATIONS_CURSOR_SIGNING_KEY_ID] = DEFAULT_OPERATIONS_CURSOR_SIGNING_KEY
    return keys


def _json_signing_keys(environ: Mapping[str, str]) -> dict[str, str]:
    raw = environ.get(OPERATIONS_CURSOR_SIGNING_KEYS_JSON_ENV)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise ValidationFailed("invalid operations cursor key configuration") from exc
    if not isinstance(parsed, dict):
        raise ValidationFailed("invalid operations cursor key configuration")
    return _string_key_map(parsed)


def _string_key_map(parsed: Mapping[object, object]) -> dict[str, str]:
    keys = {str(key): value for key, value in parsed.items() if isinstance(value, str) and str(key)}
    if len(keys) != len(parsed):
        raise ValidationFailed("invalid operations cursor key configuration")
    return keys


def _current_key_id(environ: Mapping[str, str]) -> str:
    key_id = environ.get(OPERATIONS_CURSOR_SIGNING_KEY_ID_ENV, DEFAULT_OPERATIONS_CURSOR_SIGNING_KEY_ID).strip()
    if not key_id:
        raise ValidationFailed("invalid operations cursor key configuration")
    return key_id


def _cursor_ttl_seconds(environ: Mapping[str, str]) -> int:
    raw = environ.get(OPERATIONS_CURSOR_TTL_SECONDS_ENV, str(DEFAULT_OPERATIONS_CURSOR_TTL_SECONDS))
    try:
        ttl_seconds = int(raw)
    except ValueError as exc:
        raise ValidationFailed("invalid operations cursor ttl") from exc
    if ttl_seconds < 1 or ttl_seconds > 86_400:
        raise ValidationFailed("invalid operations cursor ttl")
    return ttl_seconds


def _canonical_payload(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _unsigned_payload(payload: Mapping[str, object]) -> dict[str, object]:
    return {str(key): value for key, value in payload.items() if key != "signature"}


def _runtime_profile(environ: Mapping[str, str]) -> str:
    return str(environ.get(RUNTIME_PROFILE_ENV, "local")).strip().casefold()


def _now_epoch(now_epoch: int | None = None) -> int:
    return int(time.time()) if now_epoch is None else now_epoch


def _shape_checksum(*, run_type: RuntimeRunType, status: str | None, since: str | None, until: str | None) -> str:
    shape = {"runType": run_type, "status": status or "", "since": since or "", "until": until or ""}
    raw = json.dumps(shape, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _row_timestamp(row: RuntimeRow) -> str:
    for key in ("created_at", "started_at", "failed_at", "completed_at", "published_at", "updated_at"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    raise ValidationFailed("operations run row cannot be paged without timestamp")
