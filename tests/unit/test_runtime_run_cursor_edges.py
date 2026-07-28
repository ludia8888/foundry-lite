from __future__ import annotations

import base64
import json

import pytest
from foundry_lite.application.services.runtime_run_cursors import (
    OPERATIONS_CURSOR_PREFIX,
    OPERATIONS_CURSOR_SIGNING_KEY_ID_ENV,
    OPERATIONS_CURSOR_SIGNING_KEYS_JSON_ENV,
    OPERATIONS_CURSOR_TTL_SECONDS_ENV,
    RUNTIME_PROFILE_ENV,
    _current_key_id,
    _cursor_payload,
    _cursor_signing_key,
    _cursor_ttl_seconds,
    _json_signing_keys,
    _require_cursor_scope,
    _row_timestamp,
    _shape_checksum,
    _signed_payload,
    _string_key_map,
    decode_runtime_run_cursor,
)
from foundry_lite.domain.errors import ValidationFailed


def _encoded_payload(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return OPERATIONS_CURSOR_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_runtime_cursor_rejects_validly_signed_payload_without_page_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(RUNTIME_PROFILE_ENV, raising=False)
    payload = _signed_payload(
        {
            "actorUserId": "operator",
            "expiresAt": 20,
            "kid": "local-default",
            "runType": "action",
            "shape": "bad-shape",
            "tenantId": "tenant-a",
            "timestamp": "",
            "runId": "run-1",
        }
    )
    cursor = _encoded_payload(payload)

    with pytest.raises(ValidationFailed, match="query shape"):
        decode_runtime_run_cursor(
            cursor,
            actor_user_id="operator",
            run_type="action",
            status=None,
            since=None,
            tenant_id="tenant-a",
            until=None,
            now_epoch=10,
        )

    payload["shape"] = "invalid-after-resign"
    payload = _signed_payload(payload)
    payload["shape"] = _shape_checksum(run_type="action", status=None, since=None, until=None)
    payload = _signed_payload(payload)
    with pytest.raises(ValidationFailed, match="payload"):
        decode_runtime_run_cursor(
            _encoded_payload(payload),
            actor_user_id="operator",
            run_type="action",
            status=None,
            since=None,
            tenant_id="tenant-a",
            until=None,
            now_epoch=10,
        )


@pytest.mark.parametrize(
    "cursor",
    [
        OPERATIONS_CURSOR_PREFIX + "not-json",
        _encoded_payload([]),
        _encoded_payload({"kid": "local-default"}),
    ],
)
def test_runtime_cursor_rejects_malformed_unsigned_or_non_object_payloads(cursor: str) -> None:
    with pytest.raises(ValidationFailed):
        _cursor_payload(cursor)


def test_runtime_cursor_rejects_tampered_signature_and_unknown_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(RUNTIME_PROFILE_ENV, raising=False)
    payload = _signed_payload({"kid": "local-default", "value": 1})
    payload["value"] = 2
    with pytest.raises(ValidationFailed, match="invalid operations cursor"):
        _cursor_payload(_encoded_payload(payload))
    with pytest.raises(ValidationFailed, match="invalid operations cursor"):
        _cursor_signing_key({}, "unknown")


@pytest.mark.parametrize("expires_at", [True, "20", None])
def test_runtime_cursor_scope_requires_integer_expiry(expires_at: object) -> None:
    with pytest.raises(ValidationFailed, match="payload"):
        _require_cursor_scope(
            {"tenantId": "tenant-a", "actorUserId": "operator", "expiresAt": expires_at},
            actor_user_id="operator",
            tenant_id="tenant-a",
            now_epoch=10,
        )


def test_protected_runtime_cursor_requires_key_id_before_key_material() -> None:
    with pytest.raises(ValidationFailed, match="key id"):
        _cursor_signing_key({RUNTIME_PROFILE_ENV: "production"}, "prod-key")

    protected = {
        RUNTIME_PROFILE_ENV: "production",
        OPERATIONS_CURSOR_SIGNING_KEY_ID_ENV: "prod-key",
    }
    with pytest.raises(ValidationFailed, match="signing key is required"):
        _cursor_signing_key(protected, "prod-key")


@pytest.mark.parametrize(
    "raw",
    ["{", "[]", '{"valid":"secret","invalid":3}'],
)
def test_runtime_cursor_json_key_ring_rejects_invalid_configuration(raw: str) -> None:
    env = {OPERATIONS_CURSOR_SIGNING_KEYS_JSON_ENV: raw}
    with pytest.raises(ValidationFailed, match="key configuration"):
        _json_signing_keys(env)


def test_runtime_cursor_key_ring_and_key_id_validation() -> None:
    assert _json_signing_keys({OPERATIONS_CURSOR_SIGNING_KEYS_JSON_ENV: '{"old":"secret"}'}) == {"old": "secret"}
    with pytest.raises(ValidationFailed, match="key configuration"):
        _string_key_map({"": "secret"})
    with pytest.raises(ValidationFailed, match="key configuration"):
        _current_key_id({OPERATIONS_CURSOR_SIGNING_KEY_ID_ENV: " "})


@pytest.mark.parametrize("ttl", ["invalid", "0", "86401"])
def test_runtime_cursor_ttl_must_be_a_bounded_integer(ttl: str) -> None:
    with pytest.raises(ValidationFailed, match="cursor ttl"):
        _cursor_ttl_seconds({OPERATIONS_CURSOR_TTL_SECONDS_ENV: ttl})


def test_runtime_cursor_row_requires_a_timestamp() -> None:
    with pytest.raises(ValidationFailed, match="without timestamp"):
        _row_timestamp({"id": "run-1"})
