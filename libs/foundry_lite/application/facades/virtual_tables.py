"""Public entry point for virtual table registration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports.virtual_table import ExternalTableRef, VirtualTableRecord
from foundry_lite.application.services.virtual_table_service import (
    AutoRegistrationPlan,
    BulkRegistrationResult,
    VirtualTableService,
)
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

    def discover(
        self,
        *,
        config: Mapping[str, object],
        schema_names: tuple[str, ...] = (),
        ctx: RequestContext | None = None,
    ) -> tuple[ExternalTableRef, ...]:
        return self._service.discover_external_tables(config=config, schema_names=schema_names, ctx=ctx)

    def register_many(
        self,
        *,
        parent_rid: str,
        connection_rid: str,
        config: Mapping[str, object],
        tables: Sequence[ExternalTableRef],
        markings: tuple[str, ...] = (),
        ctx: RequestContext | None = None,
    ) -> BulkRegistrationResult:
        return self._service.register_virtual_tables(
            parent_rid=parent_rid,
            connection_rid=connection_rid,
            config=config,
            tables=tables,
            markings=markings,
            ctx=ctx,
        )

    def preview_auto_registration(
        self,
        *,
        connection_rid: str,
        config: Mapping[str, object],
        schema_names: tuple[str, ...] = (),
        ctx: RequestContext | None = None,
    ) -> AutoRegistrationPlan:
        return self._service.preview_auto_registration(
            connection_rid=connection_rid, config=config, schema_names=schema_names, ctx=ctx
        )

    def run_auto_registration(
        self,
        *,
        parent_rid: str,
        connection_rid: str,
        config: Mapping[str, object],
        schema_names: tuple[str, ...] = (),
        markings: tuple[str, ...] = (),
        ctx: RequestContext | None = None,
    ) -> BulkRegistrationResult:
        return self._service.run_auto_registration(
            parent_rid=parent_rid,
            connection_rid=connection_rid,
            config=config,
            schema_names=schema_names,
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
