"""SecretProvider port for retrieving secrets without leaking values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Protocol, runtime_checkable

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract

__all__ = ["REDACTED_VALUE", "SecretProvider", "SecretValue", "SecretVault"]

REDACTED_VALUE: Final = "***REDACTED***"


@dataclass(frozen=True)
class SecretValue:
    """Resolved secret with a stable version and non-revealing representation."""

    name: str
    version: str
    value: str = field(repr=False)

    def redacted(self) -> dict[str, str]:
        """Return operator evidence that never contains the secret value."""
        return {"name": self.name, "version": self.version, "value": REDACTED_VALUE}


@runtime_checkable
class SecretProvider(Protocol):
    """Resolve named secrets for adapters, connectors, and API entrypoints."""

    @property
    def profile_name(self) -> str: ...

    def failure_contract(self) -> AdapterFailureContract:
        """Return the adapter failure taxonomy promised by this profile."""
        ...

    def get_secret(self, name: str, *, version: str | None = None) -> SecretValue:
        """Resolve the current or explicitly versioned secret without exposing the value."""
        ...


@runtime_checkable
class SecretVault(SecretProvider, Protocol):
    """Resolve and write named secrets without exposing stored values."""

    def put_secret(
        self,
        name: str,
        value: str,
        *,
        metadata: Mapping[str, object] | None = None,
    ) -> SecretValue:
        """Store a secret and return only its redacted identity/version metadata to callers."""
        ...
