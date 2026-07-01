"""Infrastructure support for postgres rls."""

from __future__ import annotations

from weakref import WeakSet

from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine

from foundry_lite.infrastructure import schema as db
from foundry_lite.security.tenant_context import NO_TENANT_CONTEXT, current_tenant_id

_INSTALLED_ENGINES: WeakSet[Engine] = WeakSet()


def install_postgres_rls_tenant_context(engine: Engine) -> None:
    if engine in _INSTALLED_ENGINES:
        return
    event.listen(engine, "begin", _set_rls_tenant_context)
    _INSTALLED_ENGINES.add(engine)


def _set_rls_tenant_context(conn: Connection) -> None:
    tenant_id = current_tenant_id() or NO_TENANT_CONTEXT
    db.set_postgres_tenant_context(conn, tenant_id)
