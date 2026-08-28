"""Application port for deterministic OSDK artifact download token signing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract


@dataclass(frozen=True)
class OsdkDownloadTokenClaims:
    tenant_id: str
    artifact_id: str
    expires_at: str
    request_hash: str


@runtime_checkable
class OsdkDownloadTokenSigner(Protocol):
    """Sign replay-stable download tokens without exposing key storage to services."""

    @property
    def profile_name(self) -> str: ...

    def failure_contract(self) -> AdapterFailureContract:
        """Return the operator-facing failure taxonomy for this signer."""
        ...

    def sign_token(self, claims: OsdkDownloadTokenClaims) -> str:
        """Return a deterministic opaque token for the supplied claims."""
        ...
