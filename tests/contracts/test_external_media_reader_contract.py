"""Contract for ``ExternalMediaReader`` (Media/Content Plane M9).

A virtual media set reads from an external source without copying bytes. v1 ships the contract
and the version/marker invariant; the real connector is deferred, so the default reader reports
``external_reader_unavailable``. A fake reader injected by tests returns a scripted stat.
"""

from __future__ import annotations

import pytest
from foundry_lite.application.ports.external_media_reader import (
    ExternalMediaReader,
    ExternalObjectStat,
    ExternalReadError,
    ExternalReadRequest,
)
from foundry_lite.infrastructure.adapters.local_external_media_reader import LocalExternalMediaReader


def test_default_reader_reports_unavailable_and_declares_its_contract() -> None:
    adapter: ExternalMediaReader = LocalExternalMediaReader()
    with pytest.raises(ExternalReadError) as excinfo:
        adapter.stat(ExternalReadRequest(uri="s3://ext/a.pdf", expected_etag="etag-1"))
    assert excinfo.value.reason == "external_reader_unavailable"

    contract = adapter.failure_contract()
    assert contract.adapter_profile == "local-external"
    assert {mode.kind for mode in contract.modes} >= {"unavailable"}


def test_injected_connector_returns_a_scripted_stat() -> None:
    def _engine(uri: str) -> ExternalObjectStat:
        return ExternalObjectStat(uri=uri, etag="etag-2", byte_size=42, is_present=True)

    adapter = LocalExternalMediaReader(stat_engine=_engine)
    stat = adapter.stat(ExternalReadRequest(uri="s3://ext/b.pdf"))
    assert stat.etag == "etag-2"
    assert stat.byte_size == 42
    assert stat.is_present is True
