"""SQLAlchemy repository adapter for object change sequence persistence."""

from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from foundry_lite.infrastructure import schema as db


def next_object_change_sequence(transaction: Any, tenant_id: str) -> int:
    """Allocate a tenant-scoped monotonic object change sequence."""
    dialect_name = transaction.dialect.name
    if dialect_name == "postgresql":
        statement: Any = postgresql_insert(db.object_change_counters)
    elif dialect_name == "sqlite":
        statement = sqlite_insert(db.object_change_counters)
    else:
        raise RuntimeError(f"unsupported object change sequence dialect: {dialect_name}")
    result = transaction.execute(
        statement.values(tenant_id=tenant_id, last_sequence=1)
        .on_conflict_do_update(
            index_elements=[db.object_change_counters.c.tenant_id],
            set_={"last_sequence": db.object_change_counters.c.last_sequence + 1},
        )
        .returning(db.object_change_counters.c.last_sequence)
    )
    row = result.first()
    if row is None:
        raise RuntimeError("object change sequence allocation returned no row")
    return int(row[0])
