"""Small durable-row lookups for ObjectSet response projection."""

from __future__ import annotations

from foundry_lite.application.ports import ObjectSetObjectTypeRow, ObjectSetRepository, TransactionContext
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound


def object_type_by_id(
    object_set_repository: ObjectSetRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    object_type_id: str,
) -> ObjectSetObjectTypeRow:
    row = object_set_repository.object_type_by_id(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        object_type_id=object_type_id,
    )
    if row is None:
        raise NotFound("object type not found", details={"object_type_id": object_type_id})
    return row
