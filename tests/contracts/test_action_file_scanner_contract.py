from __future__ import annotations

import io

import pytest
from foundry_lite.application.ports.action_file_scanner import (
    ActionFileScanRequest,
    ActionFileScanUnavailable,
    UnavailableActionFileScanner,
)
from foundry_lite.infrastructure.adapters.action_file_scanner import LocalSignatureActionFileScanner


def _request(payload: bytes) -> ActionFileScanRequest:
    return ActionFileScanRequest(
        source=io.BytesIO(payload),
        file_name="evidence.pdf",
        supplied_mime_type="application/pdf",
        content_sha256="sha256-for-contract",
        size_bytes=len(payload),
    )


def test_action_file_scanner_returns_clean_and_rewinds_the_stream() -> None:
    request = _request(b"%PDF-1.4 clean evidence")

    result = LocalSignatureActionFileScanner().scan(request)

    assert result.verdict == "clean"
    assert result.scanner == "local-signature"
    assert request.source.tell() == 0


def test_action_file_scanner_classifies_the_standard_test_signature() -> None:
    request = _request(b"prefix EICAR-STANDARD-ANTIVIRUS-TEST-FILE suffix")

    result = LocalSignatureActionFileScanner().scan(request)

    assert result.verdict == "infected"
    assert result.threat_name == "Eicar-Test-Signature"
    assert request.source.tell() == 0


def test_unavailable_action_file_scanner_fails_closed() -> None:
    with pytest.raises(ActionFileScanUnavailable, match="not configured"):
        UnavailableActionFileScanner().scan(_request(b"clean-looking"))
