"""Opaque citation navigation token contract tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from foundry_lite.application.services.aip import citation_navigation
from foundry_lite.application.services.aip.citation_navigation import (
    CitationNavigationTokenError,
    citation_expiry,
    decode_navigation_ref,
    encode_navigation_ref,
)

_SECRET = "citation-navigation-unit-secret"


def test_navigation_token_uses_constant_time_signature_check_and_15_minute_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compare_calls: list[tuple[str, str]] = []
    original = citation_navigation.hmac.compare_digest

    def capture_compare(left: str, right: str) -> bool:
        compare_calls.append((left, right))
        return original(left, right)

    monkeypatch.setattr(citation_navigation.hmac, "compare_digest", capture_compare)
    payload = _payload()
    token = encode_navigation_ref(payload, _SECRET)
    decoded = decode_navigation_ref(
        token,
        _SECRET,
        expected_tenant_id="tenant-demo",
        observed_at=datetime(2026, 7, 16, 0, 10, tzinfo=UTC),
    )

    assert decoded == payload
    assert compare_calls and compare_calls[0][0] == compare_calls[0][1]
    assert citation_expiry("2026-07-16T00:00:00Z") == "2026-07-16T00:15:00Z"


def test_navigation_token_rejects_tampering_and_unbounded_or_inverted_lifetime() -> None:
    token = encode_navigation_ref(_payload(), _SECRET)
    tampered = f"{token[:-1]}{'0' if token[-1] != '0' else '1'}"

    with pytest.raises(CitationNavigationTokenError) as tamper_error:
        decode_navigation_ref(
            tampered,
            _SECRET,
            expected_tenant_id="tenant-demo",
            observed_at=datetime(2026, 7, 16, 0, 10, tzinfo=UTC),
        )
    with pytest.raises(CitationNavigationTokenError) as long_error:
        encode_navigation_ref(
            {**_payload(), "exp": "2026-07-16T00:15:01Z"},
            _SECRET,
        )
    with pytest.raises(CitationNavigationTokenError) as inverted_error:
        encode_navigation_ref(
            {**_payload(), "exp": "2026-07-16T00:00:00Z"},
            _SECRET,
        )

    assert tamper_error.value.reason == "invalid_navigation_signature"
    assert long_error.value.reason == "invalid_navigation_window"
    assert inverted_error.value.reason == "invalid_navigation_window"


def _payload() -> dict[str, object]:
    return {
        "tenant_id": "tenant-demo",
        "source_resource_type": "content_unit",
        "source_resource_id": "content-unit://miv-1/cu-1",
        "source_version": "miv-1",
        "content_hash": "sha256:context",
        "navigation_path": "/document-intelligence",
        "display_label": "content_unit:cu-1@miv-1",
        "evidence": {
            "mediaItemVersionId": "miv-1",
            "mediaDerivativeId": "mder-1",
            "contentUnitId": "cu-1",
            "pageNumber": 1,
        },
        "iat": "2026-07-16T00:00:00Z",
        "exp": "2026-07-16T00:15:00Z",
    }
