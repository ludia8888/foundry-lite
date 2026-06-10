from __future__ import annotations

from typing import Any, Protocol

from foundry_lite.application.ports.transaction_context import TransactionContext


class ObjectReadRepository(Protocol):
    """DB read boundary for object records and object links."""

    def object_record(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_api_name: str,
        object_id: str,
    ) -> dict[str, Any] | None:
        """Return one object record for a tenant and object reference."""
        ...

    def active_object_rows(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_api_name: str,
    ) -> list[dict[str, Any]]:
        """Return non-deleted object records for one object type, ordered by object id."""
        ...

    def active_links_from(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        link_type_api_name: str,
        from_api_name: str,
        from_object_id: str,
    ) -> list[dict[str, Any]]:
        """Return non-deleted outgoing links for one object and link type."""
        ...
