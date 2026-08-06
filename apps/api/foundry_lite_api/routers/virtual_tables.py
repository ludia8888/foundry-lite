"""Virtual table registration routes (Data Connection).

Palantir puts virtual tables under the source they belong to, in Data Connection, because a
pointer only means something relative to the connection that can resolve it. The routes follow
that shape: a table is registered against a connection and listed per connection.

Responses never carry the connection secret. `config` holds a reference the vault resolves at
read time, and the service refuses a config that carries a URL instead.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from foundry_lite.application.ports.virtual_table import ExternalTableRef, VirtualTableRecord
from foundry_lite.domain.errors import FoundryLiteError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import (
    VirtualTableAutoRegisterRequest,
    VirtualTableBulkRegisterRequest,
    VirtualTableDiscoverRequest,
    VirtualTableRegisterRequest,
)

router = APIRouter()


def _view(record: VirtualTableRecord) -> dict[str, object]:
    return {
        "rid": record.rid,
        "name": record.name,
        "parentRid": record.parent_rid,
        "connectionRid": record.connection_rid,
        "config": dict(record.config),
        "schema": {
            "columns": [
                {"name": column.name, "dataType": column.data_type, "isNullable": column.is_nullable}
                for column in record.schema.columns
            ]
        },
        "markings": list(record.markings),
        "createdAt": record.created_at,
    }


@router.post("/api/sources/{connection_rid}/virtual-tables")
def register_virtual_table(
    request: Request, connection_rid: str, payload: VirtualTableRegisterRequest
) -> dict[str, object]:
    try:
        record = runtime.foundry.virtual_tables.register(
            name=payload.name,
            parent_rid=payload.parent_rid,
            connection_rid=connection_rid,
            config=payload.config,
            markings=tuple(payload.markings),
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
    return _view(record)


@router.post("/api/sources/{connection_rid}/virtual-tables/discover")
def discover_external_tables(
    request: Request, connection_rid: str, payload: VirtualTableDiscoverRequest
) -> dict[str, object]:
    """List what the connection can reach, so a caller chooses before registering.

    A POST because the connection reference travels in the body: putting a vault path in a query
    string would place it in access logs and browser history.
    """
    del connection_rid
    try:
        tables = runtime.foundry.virtual_tables.discover(
            config=payload.config, schema_names=tuple(payload.schema_names), ctx=_ctx(request)
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
    return {"tables": [{"schema": t.schema_name, "table": t.table_name} for t in tables]}


@router.post("/api/sources/{connection_rid}/virtual-tables/bulk")
def register_virtual_tables(
    request: Request, connection_rid: str, payload: VirtualTableBulkRegisterRequest
) -> dict[str, object]:
    """Register many pointers at once. One failure does not abandon the rest."""
    try:
        result = runtime.foundry.virtual_tables.register_many(
            parent_rid=payload.parent_rid,
            connection_rid=connection_rid,
            config=payload.config,
            tables=tuple(
                ExternalTableRef(schema_name=item.schema_name, table_name=item.table_name) for item in payload.tables
            ),
            markings=tuple(payload.markings),
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
    return {
        "registered": [_view(record) for record in result.registered],
        "failures": [{"table": failure.table, "reason": failure.reason} for failure in result.failures],
    }


@router.post("/api/sources/{connection_rid}/virtual-tables/auto-registration/preview")
def preview_auto_registration(
    request: Request, connection_rid: str, payload: VirtualTableAutoRegisterRequest
) -> dict[str, object]:
    """What a scheduled pass would change. Missing tables are reported, never unregistered."""
    try:
        plan = runtime.foundry.virtual_tables.preview_auto_registration(
            connection_rid=connection_rid,
            config=payload.config,
            schema_names=tuple(payload.schema_names),
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
    return {
        "newTables": [{"schema": ref.schema_name, "table": ref.table_name} for ref in plan.new_tables],
        "missingTables": list(plan.missing_tables),
    }


@router.post("/api/sources/{connection_rid}/virtual-tables/auto-registration/run")
def run_auto_registration(
    request: Request, connection_rid: str, payload: VirtualTableAutoRegisterRequest
) -> dict[str, object]:
    """Register whatever appeared at the source since the last pass."""
    try:
        result = runtime.foundry.virtual_tables.run_auto_registration(
            parent_rid=payload.parent_rid,
            connection_rid=connection_rid,
            config=payload.config,
            schema_names=tuple(payload.schema_names),
            markings=tuple(payload.markings),
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
    return {
        "registered": [_view(record) for record in result.registered],
        "failures": [{"table": failure.table, "reason": failure.reason} for failure in result.failures],
    }


@router.get("/api/sources/{connection_rid}/virtual-tables")
def list_virtual_tables(request: Request, connection_rid: str) -> dict[str, object]:
    try:
        records = runtime.foundry.virtual_tables.list(connection_rid=connection_rid, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
    return {"virtualTables": [_view(record) for record in records]}


@router.get("/api/virtual-tables/{virtual_table_rid}")
def get_virtual_table(request: Request, virtual_table_rid: str) -> dict[str, object]:
    try:
        return _view(runtime.foundry.virtual_tables.get(virtual_table_rid, ctx=_ctx(request)))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/virtual-tables/{virtual_table_rid}/schema-drift")
def inspect_schema_drift(request: Request, virtual_table_rid: str) -> dict[str, object]:
    """Report how the source's shape differs from what was pinned. Never adopts the change."""
    try:
        return runtime.foundry.virtual_tables.schema_drift(virtual_table_rid, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.delete("/api/virtual-tables/{virtual_table_rid}")
def delete_virtual_table(request: Request, virtual_table_rid: str) -> dict[str, object]:
    """Drop the pointer. The external table is untouched -- the platform never owned it."""
    try:
        runtime.foundry.virtual_tables.delete(virtual_table_rid, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
    return {"rid": virtual_table_rid, "status": "DELETED"}
