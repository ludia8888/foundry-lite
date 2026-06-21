from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import and_, update

from foundry_lite.application.ports.transaction_context import StatusTransition


def cas_status_update(
    transaction: Any,
    table: Any,
    *,
    tenant_id: str,
    row_id: str,
    transition: StatusTransition,
    values: Mapping[str, object],
    conditions: Sequence[Any] = (),
) -> bool:
    result = transaction.execute(
        update(table)
        .where(
            and_(
                table.c.tenant_id == tenant_id,
                table.c.id == row_id,
                table.c.status.in_(transition.from_statuses),
                *conditions,
            )
        )
        .values(status=transition.to_status, **dict(values))
    )
    return result.rowcount == 1


def cas_status_update_many(
    transaction: Any,
    table: Any,
    *,
    tenant_id: str,
    transition: StatusTransition,
    values: Mapping[str, object],
    conditions: Sequence[Any],
) -> int:
    result = transaction.execute(
        update(table)
        .where(
            and_(
                table.c.tenant_id == tenant_id,
                table.c.status.in_(transition.from_statuses),
                *conditions,
            )
        )
        .values(status=transition.to_status, **dict(values))
    )
    return int(result.rowcount or 0)
