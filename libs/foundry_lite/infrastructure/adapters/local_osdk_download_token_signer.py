"""Local HMAC signer for OSDK artifact download tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
from pathlib import Path

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureKind,
    AdapterFailureMode,
)
from foundry_lite.application.ports.osdk_download_token_signer import OsdkDownloadTokenClaims


class LocalOsdkDownloadTokenSigner:
    """Sign tokens with a persistent key created once using exclusive file creation."""

    profile_name = "local-osdk-download-token-signer"

    def __init__(self, key_path: str | Path) -> None:
        self._key_path = Path(key_path)

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    operation="sign_token",
                    kind="validation",
                    is_retryable=False,
                    operator_message="OSDK download token signing key is invalid; restore a valid key.",
                ),
                AdapterFailureMode(
                    operation="sign_token",
                    kind="unavailable",
                    is_retryable=True,
                    operator_message="OSDK download token signer is unavailable; retry the same request.",
                ),
            ),
        )

    def sign_token(self, claims: OsdkDownloadTokenClaims) -> str:
        payload = {
            "tenantId": claims.tenant_id,
            "artifactId": claims.artifact_id,
            "expiresAt": claims.expires_at,
            "requestHash": claims.request_hash,
        }
        body = _base64_url(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        signature = _base64_url(hmac.new(self._signing_key(), body.encode("ascii"), hashlib.sha256).digest())
        return f"osdkdl.{body}.{signature}"

    def _signing_key(self) -> bytes:
        try:
            encoded = self._key_path.read_bytes()
        except FileNotFoundError:
            encoded = self._create_key_once()
        except OSError as exc:
            raise self._error(
                "unavailable",
                True,
                "OSDK download token signer is unavailable; retry the same request.",
            ) from exc
        try:
            key = base64.urlsafe_b64decode(encoded)
        except ValueError as exc:
            raise self._invalid_key_error() from exc
        if len(key) != 32:
            raise self._invalid_key_error()
        return key

    def _create_key_once(self) -> bytes:
        try:
            self._key_path.parent.mkdir(parents=True, exist_ok=True)
            encoded = base64.urlsafe_b64encode(secrets.token_bytes(32))
            with tempfile.NamedTemporaryFile("wb", dir=self._key_path.parent) as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
                try:
                    os.link(stream.name, self._key_path)
                except FileExistsError:
                    return self._read_concurrently_created_key()
                return encoded
        except OSError as exc:
            raise self._error(
                "unavailable",
                True,
                "OSDK download token signer is unavailable; retry the same request.",
            ) from exc

    def _read_concurrently_created_key(self) -> bytes:
        try:
            return self._key_path.read_bytes()
        except OSError as exc:
            raise self._error(
                "unavailable",
                True,
                "OSDK download token signer is unavailable; retry the same request.",
            ) from exc

    def _invalid_key_error(self) -> AdapterError:
        return self._error(
            "validation",
            False,
            "OSDK download token signing key is invalid; restore a valid key.",
        )

    def _error(self, kind: AdapterFailureKind, is_retryable: bool, message: str) -> AdapterError:
        return AdapterError(
            AdapterFailure(
                adapter_profile=self.profile_name,
                operation="sign_token",
                kind=kind,
                is_retryable=is_retryable,
                operator_message=message,
            )
        )


def _base64_url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")
