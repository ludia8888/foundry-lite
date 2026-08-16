"""Application service helpers for runtime run paging workflows."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

from foundry_lite.application.ports import (
    RuntimeRepository,
    RuntimeRow,
    RuntimeRunPageCursor,
    RuntimeRunQueryResult,
    RuntimeRunType,
)
from foundry_lite.application.services.runtime_run_cursors import (
    decode_runtime_run_cursor,
    encode_runtime_run_cursor,
)
from foundry_lite.application.services.runtime_run_projection import operator_safe_run_row
from foundry_lite.application.services.runtime_run_queries import RUN_GROUPS
from foundry_lite.domain.errors import ValidationFailed

OPERATIONS_RUN_DEFAULT_LIMIT = 50
OPERATIONS_RUN_MAX_LIMIT = 500


def query_runtime_run_page(
    runtime_repository: RuntimeRepository,
    *,
    actor_user_id: str,
    tenant_id: str,
    run_type: RuntimeRunType | None,
    status: str | None,
    since: str | None,
    until: str | None,
    limit: int,
    cursor: str | None,
) -> RuntimeRunQueryResult:
    query_limit = _operations_run_limit(limit)
    if run_type is not None:
        return _query_run_type(
            runtime_repository,
            actor_user_id,
            tenant_id,
            run_type,
            status,
            since,
            until,
            query_limit,
            cursor,
        )
    if cursor:
        raise ValidationFailed("operations cursor requires runType")
    return _query_all_run_types(runtime_repository, actor_user_id, tenant_id, status, since, until, query_limit)


def _query_run_type(
    runtime_repository: RuntimeRepository,
    actor_user_id: str,
    tenant_id: str,
    run_type: RuntimeRunType,
    status: str | None,
    since: str | None,
    until: str | None,
    limit: int,
    cursor: str | None,
) -> RuntimeRunQueryResult:
    cursor_state = _decode_run_cursor(cursor, actor_user_id, tenant_id, run_type, status, since, until)
    rows = runtime_repository.query_run_rows(
        tenant_id=tenant_id,
        run_type=run_type,
        status=status,
        since=since,
        until=until,
        cursor=cursor_state,
        limit=limit + 1,
    )
    result = _empty_run_query_result()
    page = rows[:limit]
    _set_run_group(result, run_type, [operator_safe_run_row(row, run_type) for row in page])
    result["nextCursor"] = _next_run_cursor(
        page,
        len(rows) > limit,
        actor_user_id,
        tenant_id,
        run_type,
        status,
        since,
        until,
    )
    return result


def _decode_run_cursor(
    cursor: str | None,
    actor_user_id: str,
    tenant_id: str,
    run_type: RuntimeRunType,
    status: str | None,
    since: str | None,
    until: str | None,
) -> RuntimeRunPageCursor | None:
    return decode_runtime_run_cursor(
        cursor,
        actor_user_id=actor_user_id,
        run_type=run_type,
        status=status,
        since=since,
        tenant_id=tenant_id,
        until=until,
    )


def _query_all_run_types(
    runtime_repository: RuntimeRepository,
    actor_user_id: str,
    tenant_id: str,
    status: str | None,
    since: str | None,
    until: str | None,
    limit: int,
) -> RuntimeRunQueryResult:
    result = _empty_run_query_result()
    next_cursors: dict[RuntimeRunType, str] = {}
    for current_type in RUN_GROUPS:
        rows = runtime_repository.query_run_rows(
            tenant_id=tenant_id,
            run_type=current_type,
            status=status,
            since=since,
            until=until,
            cursor=None,
            limit=limit + 1,
        )
        page = rows[:limit]
        _set_run_group(result, current_type, [operator_safe_run_row(row, current_type) for row in page])
        next_cursor = _next_run_cursor(
            page,
            len(rows) > limit,
            actor_user_id,
            tenant_id,
            current_type,
            status,
            since,
            until,
        )
        if next_cursor:
            next_cursors[current_type] = next_cursor
    if next_cursors:
        result["nextCursors"] = next_cursors
    return result


def _operations_run_limit(limit: int) -> int:
    if limit < 1:
        raise ValidationFailed("operations run limit must be positive", details={"limit": limit})
    if limit > OPERATIONS_RUN_MAX_LIMIT:
        raise ValidationFailed(
            "operations run limit exceeds maximum",
            details={"limit": limit, "max_limit": OPERATIONS_RUN_MAX_LIMIT},
        )
    return limit


def _empty_run_query_result() -> RuntimeRunQueryResult:
    return {
        "sourceExplorationRuns": [],
        "syncRuns": [],
        "transformRuns": [],
        "indexRuns": [],
        "actionRuns": [],
        "actionWritebacks": [],
        "materializationRuns": [],
        "outboxEvents": [],
        "deadLetterEvents": [],
        "workflowRuns": [],
        "aiRuns": [],
        "auditEvents": [],
        "objectEdits": [],
    }


def _set_run_group(result: RuntimeRunQueryResult, run_type: RuntimeRunType, rows: list[RuntimeRow]) -> None:
    cast(dict[str, object], result)[RUN_GROUPS[run_type]] = rows


def _next_run_cursor(
    page: Sequence[RuntimeRow],
    has_more: bool,
    actor_user_id: str,
    tenant_id: str,
    run_type: RuntimeRunType,
    status: str | None,
    since: str | None,
    until: str | None,
) -> str | None:
    if not has_more or not page:
        return None
    return encode_runtime_run_cursor(
        page[-1],
        actor_user_id=actor_user_id,
        run_type=run_type,
        status=status,
        since=since,
        tenant_id=tenant_id,
        until=until,
    )
