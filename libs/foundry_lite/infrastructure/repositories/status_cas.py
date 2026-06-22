from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import and_, select, update

from foundry_lite.application.ports.transaction_context import StatusTransition, TransitionResult
from foundry_lite.application.state_transitions import classify_transition_miss, transition_updated


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
    return cas_status_transition(
        transaction,
        table,
        tenant_id=tenant_id,
        row_id=row_id,
        transition=transition,
        values=values,
        conditions=conditions,
    ).updated


def cas_status_transition(
    transaction: Any,
    table: Any,
    *,
    tenant_id: str,
    row_id: str,
    transition: StatusTransition,
    values: Mapping[str, object],
    conditions: Sequence[Any] = (),
) -> TransitionResult:
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
    if result.rowcount == 1:
        return transition_updated(transition)
    return classify_transition_miss(transition, _current_status(transaction, table, tenant_id, row_id))


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


def cas_status_guarded_update(
    transaction: Any,
    table: Any,
    *,
    tenant_id: str,
    row_id: str,
    allowed_statuses: Sequence[str],
    values: Mapping[str, object],
    conditions: Sequence[Any] = (),
) -> bool:
    """Update non-status fields only while the row is still in an allowed state."""
    result = transaction.execute(
        update(table)
        .where(
            and_(
                table.c.tenant_id == tenant_id,
                table.c.id == row_id,
                table.c.status.in_(allowed_statuses),
                *conditions,
            )
        )
        .values(**dict(values))
    )
    return result.rowcount == 1


def _current_status(transaction: Any, table: Any, tenant_id: str, row_id: str) -> str | None:
    row = (
        transaction.execute(select(table.c.status).where(and_(table.c.tenant_id == tenant_id, table.c.id == row_id)))
        .mappings()
        .first()
    )
    if row is None:
        return None
    status = row["status"]
    return str(status) if status is not None else None
