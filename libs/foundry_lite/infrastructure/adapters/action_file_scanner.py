"""Local signature and ClamAV adapters for Action upload malware scanning."""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass

from foundry_lite.application.ports.action_file_scanner import (
    ActionFileScanRequest,
    ActionFileScanResult,
    ActionFileScanUnavailable,
)

_EICAR_MARKER = b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE"
_MAX_RESPONSE_BYTES = 16 * 1024


class LocalSignatureActionFileScanner:
    """Deterministic development scanner; never used by protected runtimes."""

    profile_name = "local-signature"

    def scan(self, request: ActionFileScanRequest) -> ActionFileScanResult:
        try:
            request.source.seek(0)
            is_infected = _contains_marker(request.source, _EICAR_MARKER)
        finally:
            request.source.seek(0)
        return ActionFileScanResult(
            verdict="infected" if is_infected else "clean",
            scanner=self.profile_name,
            engine_version="foundry-lite-dev-1",
            signature_database_version="eicar-1",
            threat_name="Eicar-Test-Signature" if is_infected else None,
        )


@dataclass(frozen=True, slots=True)
class ClamAvActionFileScannerConfig:
    host: str
    port: int = 3310
    timeout_seconds: float = 15.0
    chunk_bytes: int = 1024 * 1024


class ClamAvActionFileScanner:
    """Stream uploads to a separately managed clamd service using INSTREAM."""

    profile_name = "clamav"

    def __init__(self, config: ClamAvActionFileScannerConfig) -> None:
        self._config = config

    def scan(self, request: ActionFileScanRequest) -> ActionFileScanResult:
        try:
            request.source.seek(0)
            with socket.create_connection(
                (self._config.host, self._config.port), timeout=self._config.timeout_seconds
            ) as connection:
                connection.settimeout(self._config.timeout_seconds)
                connection.sendall(b"zINSTREAM\0")
                _send_stream(connection, request, self._config.chunk_bytes)
                response = _receive_response(connection)
        except (OSError, ValueError) as exc:
            raise ActionFileScanUnavailable("ClamAV scan did not return a trusted verdict") from exc
        finally:
            request.source.seek(0)
        return _parse_clamav_response(response)


def _contains_marker(source: object, marker: bytes) -> bool:
    carry = b""
    while chunk := source.read(1024 * 1024):  # type: ignore[attr-defined]
        combined = carry + chunk
        if marker in combined:
            return True
        carry = combined[-(len(marker) - 1) :]
    return False


def _send_stream(connection: socket.socket, request: ActionFileScanRequest, chunk_bytes: int) -> None:
    streamed = 0
    while chunk := request.source.read(chunk_bytes):
        streamed += len(chunk)
        if streamed > request.size_bytes:
            raise ValueError("upload changed while it was being scanned")
        connection.sendall(struct.pack(">I", len(chunk)))
        connection.sendall(chunk)
    if streamed != request.size_bytes:
        raise ValueError("upload size changed while it was being scanned")
    connection.sendall(struct.pack(">I", 0))


def _receive_response(connection: socket.socket) -> str:
    payload = bytearray()
    while len(payload) < _MAX_RESPONSE_BYTES:
        chunk = connection.recv(min(4096, _MAX_RESPONSE_BYTES - len(payload)))
        if not chunk:
            break
        payload.extend(chunk)
        if b"\0" in chunk or b"\n" in chunk:
            break
    if not payload:
        raise ValueError("ClamAV returned an empty response")
    return bytes(payload).rstrip(b"\0\r\n").decode("utf-8", errors="strict")


def _parse_clamav_response(response: str) -> ActionFileScanResult:
    if response.endswith(": OK"):
        return ActionFileScanResult(verdict="clean", scanner="clamav")
    prefix, separator, suffix = response.rpartition(": ")
    del prefix
    if separator and suffix.endswith(" FOUND"):
        threat_name = suffix.removesuffix(" FOUND").strip()
        if threat_name:
            return ActionFileScanResult(verdict="infected", scanner="clamav", threat_name=threat_name)
    raise ActionFileScanUnavailable("ClamAV response could not be classified safely")


__all__ = [
    "ClamAvActionFileScanner",
    "ClamAvActionFileScannerConfig",
    "LocalSignatureActionFileScanner",
]
