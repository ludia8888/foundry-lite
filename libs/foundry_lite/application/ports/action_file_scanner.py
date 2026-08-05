"""Port for fail-closed malware scanning of Action parameter uploads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Literal, Protocol

ActionFileScanVerdict = Literal["clean", "infected"]


@dataclass(frozen=True, slots=True)
class ActionFileScanRequest:
    """One bounded upload presented to a scanner without storage credentials."""

    source: BinaryIO
    file_name: str
    supplied_mime_type: str
    content_sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class ActionFileScanResult:
    """Redacted evidence safe to persist with the immutable media version."""

    verdict: ActionFileScanVerdict
    scanner: str
    engine_version: str | None = None
    signature_database_version: str | None = None
    threat_name: str | None = None


class ActionFileScanUnavailable(RuntimeError):
    """The configured scanner could not prove a clean verdict."""


class ActionFileScanner(Protocol):
    """Scan upload bytes and return only a classified, redacted verdict."""

    @property
    def profile_name(self) -> str: ...

    def scan(self, request: ActionFileScanRequest) -> ActionFileScanResult: ...


class UnavailableActionFileScanner:
    """Fail-closed default for compositions that omitted scanner wiring."""

    profile_name = "unavailable-action-file-scanner"

    def scan(self, request: ActionFileScanRequest) -> ActionFileScanResult:
        del request
        raise ActionFileScanUnavailable("Action file scanner is not configured")


__all__ = [
    "ActionFileScanner",
    "ActionFileScanRequest",
    "ActionFileScanResult",
    "ActionFileScanUnavailable",
    "ActionFileScanVerdict",
    "UnavailableActionFileScanner",
]
