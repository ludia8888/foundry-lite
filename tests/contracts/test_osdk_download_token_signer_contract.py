"""Contract tests for OSDK download token signers."""

from __future__ import annotations

import base64
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.osdk_download_token_signer import OsdkDownloadTokenClaims
from foundry_lite.infrastructure.adapters.local_osdk_download_token_signer import LocalOsdkDownloadTokenSigner


def _claims() -> OsdkDownloadTokenClaims:
    return OsdkDownloadTokenClaims(
        tenant_id="tenant-demo",
        artifact_id="artifact-1",
        expires_at="2026-08-28T12:00:00+00:00",
        request_hash="sha256:request",
    )


def test_local_osdk_download_token_signer_is_deterministic_and_persists_restricted_key(tmp_path: Path) -> None:
    key_path = tmp_path / "token.key"
    signer = LocalOsdkDownloadTokenSigner(key_path)

    first = signer.sign_token(_claims())
    second = LocalOsdkDownloadTokenSigner(key_path).sign_token(_claims())

    assert first == second
    assert first.startswith("osdkdl.")
    assert len(base64.urlsafe_b64decode(key_path.read_bytes())) == 32
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_local_osdk_download_token_signer_creates_one_key_under_concurrency(tmp_path: Path) -> None:
    key_path = tmp_path / "token.key"

    def sign_once(_: int) -> str:
        return LocalOsdkDownloadTokenSigner(key_path).sign_token(_claims())

    with ThreadPoolExecutor(max_workers=8) as executor:
        tokens = list(executor.map(sign_once, range(32)))

    assert len(set(tokens)) == 1
    assert list(tmp_path.glob("tmp*")) == []


def test_local_osdk_download_token_signer_rejects_corrupt_key_without_exposing_value(tmp_path: Path) -> None:
    key_path = tmp_path / "customer-secret.key"
    key_path.write_text("not-a-valid-key", encoding="utf-8")

    with pytest.raises(AdapterError) as captured:
        LocalOsdkDownloadTokenSigner(key_path).sign_token(_claims())

    assert captured.value.failure.kind == "validation"
    assert captured.value.failure.is_retryable is False
    assert "customer-secret" not in str(captured.value.details)
    assert "not-a-valid-key" not in str(captured.value.details)


def test_local_osdk_download_token_signer_declares_failure_contract(tmp_path: Path) -> None:
    contract = LocalOsdkDownloadTokenSigner(tmp_path / "token.key").failure_contract()

    assert contract.adapter_profile == "local-osdk-download-token-signer"
    assert [(mode.operation, mode.kind, mode.is_retryable) for mode in contract.modes] == [
        ("sign_token", "validation", False),
        ("sign_token", "unavailable", True),
    ]
