"""External source boundary for virtual media sets (Media/Content Plane M9).

A virtual media set reads directly from an external source system without copying bytes into
the backing store (ADR-0001 invariant: ``external_virtual_object_requires_version_or_mutable_
marker``). Because a virtual set "is not aware of updates/deletions in the source", an external
version must either pin a source ``etag``/version (immutable — re-validated on read, mismatch
fails closed) or be explicitly marked a mutable external reference (its freshness is surfaced,
never silently swapped). The real connector is deferred — the default reader reports
``external_reader_unavailable`` (mirroring live-OCR / live-ASR), and tests inject a fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract


@dataclass(frozen=True)
class ExternalReadRequest:
    """A request to stat one external object, optionally re-validating a pinned etag."""

    uri: str
    expected_etag: str | None = None


@dataclass(frozen=True)
class ExternalObjectStat:
    """External-object existence + current etag/size, used to detect drift from a pinned version."""

    uri: str
    etag: str
    byte_size: int
    is_present: bool


class ExternalReadError(Exception):
    """Raised when an external object is unreachable or no external connector is bundled."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ExternalMediaReader(Protocol):
    """Read boundary for virtual/external media (no copy into the backing store)."""

    @property
    def profile_name(self) -> str: ...

    def stat(self, request: ExternalReadRequest) -> ExternalObjectStat:
        """Stat an external object (existence + current etag), or raise ``ExternalReadError``."""
        ...

    def failure_contract(self) -> AdapterFailureContract:
        """Declare the typed failure modes this reader fails closed on."""
        ...
