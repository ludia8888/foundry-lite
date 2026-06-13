from __future__ import annotations

from collections.abc import Sequence

from foundry_lite.application.ports import (
    RuntimeRepository,
    RuntimeRow,
    RuntimeRunQueryResult,
    RuntimeRunType,
)
from foundry_lite.application.services.runtime_run_cursors import (
    decode_runtime_run_cursor,
    encode_runtime_run_cursor,
)
from foundry_lite.application.services.runtime_run_queries import RUN_GROUPS
from foundry_lite.domain.errors import ValidationFailed

OPERATIONS_RUN_DEFAULT_LIMIT = 50
OPERATIONS_RUN_MAX_LIMIT = 500


def query_runtime_run_page(
    runtime_repository: RuntimeRepository,
    *,
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
        return _query_run_type(runtime_repository, tenant_id, run_type, status, since, until, query_limit, cursor)
    if cursor:
        raise ValidationFailed("operations cursor requires runType")
    return _query_all_run_types(runtime_repository, tenant_id, status, since, until, query_limit)


def _query_run_type(
    runtime_repository: RuntimeRepository,
    tenant_id: str,
    run_type: RuntimeRunType,
    status: str | None,
    since: str | None,
    until: str | None,
    limit: int,
    cursor: str | None,
) -> RuntimeRunQueryResult:
    cursor_state = decode_runtime_run_cursor(cursor, run_type=run_type, status=status, since=since, until=until)
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
    _set_run_group(result, run_type, page)
    result["nextCursor"] = _next_run_cursor(page, len(rows) > limit, run_type, status, since, until)
    return result


def _query_all_run_types(
    runtime_repository: RuntimeRepository,
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
        _set_run_group(result, current_type, page)
        next_cursor = _next_run_cursor(page, len(rows) > limit, current_type, status, since, until)
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
        "syncRuns": [],
        "transformRuns": [],
        "indexRuns": [],
        "actionRuns": [],
        "actionWritebacks": [],
        "materializationRuns": [],
        "outboxEvents": [],
        "deadLetterEvents": [],
        "auditEvents": [],
        "objectEdits": [],
    }


def _set_run_group(result: RuntimeRunQueryResult, run_type: RuntimeRunType, rows: list[RuntimeRow]) -> None:
    if run_type == "sync":
        result["syncRuns"] = rows
    elif run_type == "transform":
        result["transformRuns"] = rows
    elif run_type == "index":
        result["indexRuns"] = rows
    elif run_type == "action":
        result["actionRuns"] = rows
    elif run_type == "action_writeback":
        result["actionWritebacks"] = rows
    elif run_type == "materialization":
        result["materializationRuns"] = rows
    elif run_type == "outbox":
        result["outboxEvents"] = rows
    elif run_type == "dead_letter":
        result["deadLetterEvents"] = rows
    else:
        result["auditEvents"] = rows


def _next_run_cursor(
    page: Sequence[RuntimeRow],
    has_more: bool,
    run_type: RuntimeRunType,
    status: str | None,
    since: str | None,
    until: str | None,
) -> str | None:
    if not has_more or not page:
        return None
    return encode_runtime_run_cursor(page[-1], run_type=run_type, status=status, since=since, until=until)
