"""Local external media reader (Media/Content Plane M9, doc §M9).

A virtual media set reads from an external source system without copying bytes into the
backing store. v1 ships the contract + the version/marker invariant; the real connector (S3,
HTTP, an external object store) is deferred — so the default reader reports
``external_reader_unavailable`` (mirroring live-OCR / live-ASR). Tests inject a fake reader to
exercise etag re-validation and freshness without a real external system.
"""

from __future__ import annotations

from collections.abc import Callable

from foundry_lite.application.ports.adapter_failure import (
    AdapterFailureContract,
    AdapterFailureMode,
)
from foundry_lite.application.ports.external_media_reader import (
    ExternalObjectStat,
    ExternalReadError,
    ExternalReadRequest,
)

# uri -> current external stat (raises ExternalReadError when no connector is bundled).
ExternalStatEngine = Callable[[str], ExternalObjectStat]


def _default_external_stat(uri: str) -> ExternalObjectStat:
    # No external connector is bundled; a real profile injects one. Shipping the contract +
    # marker invariant now and deferring the live connector mirrors the live-OCR deferral.
    del uri
    raise ExternalReadError("external_reader_unavailable")


class LocalExternalMediaReader:
    """``ExternalMediaReader`` whose connector is injectable (profile ``local-external``)."""

    profile_name = "local-external"

    def __init__(self, *, stat_engine: ExternalStatEngine | None = None) -> None:
        self._stat_engine = stat_engine or _default_external_stat

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "stat",
                    "unavailable",
                    True,
                    "No external connector is bundled, or the external source is unreachable; retry bounded.",
                ),
            ),
        )

    def stat(self, request: ExternalReadRequest) -> ExternalObjectStat:
        return self._stat_engine(request.uri)
