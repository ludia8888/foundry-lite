from __future__ import annotations

import io
from typing import Self

import pytest
from foundry_lite.application.ports.action_file_scanner import (
    ActionFileScanRequest,
    ActionFileScanUnavailable,
)
from foundry_lite.infrastructure.adapters import action_file_scanner as scanner_module
from foundry_lite.infrastructure.adapters.action_file_scanner import (
    ClamAvActionFileScanner,
    ClamAvActionFileScannerConfig,
)


class _ClamSocket:
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.sent = bytearray()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def settimeout(self, _timeout: float) -> None:
        return None

    def sendall(self, payload: bytes) -> None:
        self.sent.extend(payload)

    def recv(self, _size: int) -> bytes:
        response, self.response = self.response, b""
        return response


def _request(payload: bytes) -> ActionFileScanRequest:
    return ActionFileScanRequest(io.BytesIO(payload), "receipt.pdf", "application/pdf", "digest", len(payload))


@pytest.mark.parametrize(
    ("response", "verdict", "threat_name"),
    [
        (b"stream: OK\0", "clean", None),
        (b"stream: Eicar-Test-Signature FOUND\0", "infected", "Eicar-Test-Signature"),
    ],
)
def test_clamav_scanner_streams_bounded_bytes_and_classifies_response(
    monkeypatch: pytest.MonkeyPatch,
    response: bytes,
    verdict: str,
    threat_name: str | None,
) -> None:
    connection = _ClamSocket(response)
    monkeypatch.setattr(scanner_module.socket, "create_connection", lambda *_args, **_kwargs: connection)
    request = _request(b"%PDF evidence")

    result = ClamAvActionFileScanner(ClamAvActionFileScannerConfig(host="clamav.internal")).scan(request)

    assert result.verdict == verdict
    assert result.threat_name == threat_name
    assert connection.sent.startswith(b"zINSTREAM\0")
    assert connection.sent.endswith(b"\0\0\0\0")
    assert request.source.tell() == 0


def test_clamav_scanner_rejects_unknown_or_empty_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _ClamSocket(b"stream: scanner error\0")
    monkeypatch.setattr(scanner_module.socket, "create_connection", lambda *_args, **_kwargs: connection)

    with pytest.raises(ActionFileScanUnavailable, match="classified safely"):
        ClamAvActionFileScanner(ClamAvActionFileScannerConfig(host="clamav.internal")).scan(_request(b"data"))
