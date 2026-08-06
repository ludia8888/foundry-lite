"""Virtual table registration routes (Data Connection).

Palantir puts virtual tables under the source they belong to, in Data Connection, because a
pointer only means something relative to the connection that can resolve it. The routes follow
that shape: a table is registered against a connection and listed per connection.

Responses never carry the connection secret. `config` holds a reference the vault resolves at
read time, and the service refuses a config that carries a URL instead.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from foundry_lite.application.ports.virtual_table import VirtualTableRecord
from foundry_lite.domain.errors import FoundryLiteError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import VirtualTableRegisterRequest

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
