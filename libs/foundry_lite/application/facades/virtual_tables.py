"""Public entry point for virtual table registration."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.virtual_table import VirtualTableRecord
from foundry_lite.application.services.virtual_table_service import VirtualTableService
from foundry_lite.domain.context import RequestContext


class VirtualTableGateway:
    """Register and inspect pointers to tables in external systems."""

    def __init__(self, service: VirtualTableService) -> None:
        self._service = service

    def register(
        self,
        *,
        name: str,
        parent_rid: str,
        connection_rid: str,
        config: Mapping[str, object],
        markings: tuple[str, ...] = (),
        ctx: RequestContext | None = None,
    ) -> VirtualTableRecord:
        return self._service.register_virtual_table(
            name=name,
            parent_rid=parent_rid,
            connection_rid=connection_rid,
            config=config,
            markings=markings,
            ctx=ctx,
        )

    def list(self, *, connection_rid: str, ctx: RequestContext | None = None) -> tuple[VirtualTableRecord, ...]:
        return self._service.list_virtual_tables(connection_rid=connection_rid, ctx=ctx)

    def get(self, rid: str, *, ctx: RequestContext | None = None) -> VirtualTableRecord:
        return self._service.get_virtual_table(rid, ctx=ctx)

    def schema_drift(self, rid: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._service.inspect_schema_drift(rid, ctx=ctx)

    def delete(self, rid: str, *, ctx: RequestContext | None = None) -> None:
        self._service.delete_virtual_table(rid, ctx=ctx)
