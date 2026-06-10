from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ObjectSetRecord:
    set_id: str
    tenant_id: str
    name: str
    object_type_id: str
    set_type: str
    definition: dict[str, Any]
    visibility: str
    owner_user_id: str
    expires_at: str | None
    created_at: str


class ObjectSetRepository(Protocol):
    """DB boundary for object set rows and supporting object membership reads."""

    def create_object_set(self, *, transaction: Any, record: ObjectSetRecord) -> None:
        """Persist a new object set."""
        ...

    def object_set_by_id(self, *, transaction: Any, tenant_id: str, set_id: str) -> dict[str, Any] | None:
        """Return one object set row for a tenant."""
        ...

    def object_sets(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return object set rows for a tenant, optionally narrowed to one object type."""
        ...

    def delete_object_sets(self, *, transaction: Any, set_ids: list[str]) -> None:
        """Delete object sets by id."""
        ...

    def active_object_ids(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        object_ids: list[str],
    ) -> set[str]:
        """Return active object ids that exist for a requested id list."""
        ...

    def active_object_records_by_ids(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_api_name: str,
        object_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Return active object records for a requested id list."""
        ...

    def property_names_for_object_type(self, *, transaction: Any, object_type_id: str) -> set[str]:
        """Return property API names for one object type."""
        ...

    def object_type_by_id(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        object_type_id: str,
    ) -> dict[str, Any] | None:
        """Return one object type row for a tenant."""
        ...
