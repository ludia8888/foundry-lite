"""Application port contract for metadata repository."""

from __future__ import annotations

from typing import Protocol


class MetadataRepository(Protocol):
    """DB boundary for core metadata bootstrap."""

    def initialize_schema(self) -> None:
        """Create metadata tables when the local runtime starts."""
        ...

    def ensure_tenant(self, *, tenant_id: str, name: str, created_at: str) -> None:
        """Create a tenant row if it is missing."""
        ...

    def ensure_user(
        self,
        *,
        user_id: str,
        tenant_id: str,
        email: str,
        roles: list[str],
        created_at: str,
    ) -> None:
        """Create a user row if it is missing."""
        ...
