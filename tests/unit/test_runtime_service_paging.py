from __future__ import annotations

import pytest
from foundry_lite.application.ports import RuntimeRow, RuntimeRunPageCursor, RuntimeRunType
from foundry_lite.application.services.runtime_run_cursors import encode_runtime_run_cursor
from foundry_lite.application.services.runtime_service import RuntimeService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


class _PagedRuntimeRepository:
    def __init__(self) -> None:
        self.requested_limits: list[int] = []
        self.rows = [
            _runtime_row("action_c", "2026-06-10T00:00:02Z"),
            _runtime_row("action_b", "2026-06-10T00:00:02Z"),
            _runtime_row("action_a", "2026-06-10T00:00:01Z"),
        ]

    def list_runs(self, *, tenant_id: str) -> object:
        del tenant_id
        raise AssertionError("operations query must not read the full run snapshot")

    def query_run_rows(
        self,
        *,
        tenant_id: str,
        run_type: RuntimeRunType,
        status: str | None,
        since: str | None,
        until: str | None,
        cursor: RuntimeRunPageCursor | None,
        limit: int,
    ) -> list[RuntimeRow]:
        del tenant_id, status, since, until
        assert run_type == "action"
        self.requested_limits.append(limit)
        rows = self.rows
        if cursor is not None:
            rows = [
                row
                for row in rows
                if (str(row["created_at"]), str(row["id"])) < (cursor["timestamp"], cursor["run_id"])
            ]
        return rows[:limit]


class _AllRunTypesRepository:
    def __init__(self) -> None:
        self.requested_types: list[RuntimeRunType] = []

    def query_run_rows(
        self,
        *,
        tenant_id: str,
        run_type: RuntimeRunType,
        status: str | None,
        since: str | None,
        until: str | None,
        cursor: RuntimeRunPageCursor | None,
        limit: int,
    ) -> list[RuntimeRow]:
        del tenant_id, status, since, until, cursor
        self.requested_types.append(run_type)
        return [
            _runtime_row(f"{run_type}_b", "2026-06-10T00:00:02Z"),
            _runtime_row(f"{run_type}_a", "2026-06-10T00:00:01Z"),
        ][:limit]


class _UnusedPolicy:
    pass


class _UnusedEngine:
    pass


def test_runtime_service_query_runs_uses_db_keyset_page_and_opaque_cursor() -> None:
    repository = _PagedRuntimeRepository()
    service = RuntimeService(engine=_UnusedEngine(), policy=_UnusedPolicy(), runtime_repository=repository)

    first = service.query_runs(
        ctx=RequestContext(roles=("ops_manager",)),
        run_type="action",
        status="succeeded",
        limit=1,
    )
    second = service.query_runs(
        ctx=RequestContext(roles=("ops_manager",)),
        run_type="action",
        status="succeeded",
        limit=1,
        cursor=first["nextCursor"],
    )

    assert repository.requested_limits == [2, 2]
    assert [row["id"] for row in first["actionRuns"]] == ["action_c"]
    assert [row["id"] for row in second["actionRuns"]] == ["action_b"]
    assert isinstance(first["nextCursor"], str)


def test_runtime_service_query_runs_builds_group_next_cursors() -> None:
    repository = _AllRunTypesRepository()
    service = RuntimeService(engine=_UnusedEngine(), policy=_UnusedPolicy(), runtime_repository=repository)

    result = service.query_runs(ctx=RequestContext(roles=("ops_manager",)), limit=1)

    assert [row["id"] for row in result["syncRuns"]] == ["sync_b"]
    assert [row["id"] for row in result["transformRuns"]] == ["transform_b"]
    assert [row["id"] for row in result["indexRuns"]] == ["index_b"]
    assert [row["id"] for row in result["actionRuns"]] == ["action_b"]
    assert [row["id"] for row in result["actionWritebacks"]] == ["action_writeback_b"]
    assert [row["id"] for row in result["materializationRuns"]] == ["materialization_b"]
    assert [row["id"] for row in result["outboxEvents"]] == ["outbox_b"]
    assert [row["id"] for row in result["deadLetterEvents"]] == ["dead_letter_b"]
    assert [row["id"] for row in result["auditEvents"]] == ["audit_b"]
    assert set(result["nextCursors"]) == set(repository.requested_types)


def test_runtime_service_query_runs_rejects_bad_cursor_and_large_limit() -> None:
    service = RuntimeService(
        engine=_UnusedEngine(),
        policy=_UnusedPolicy(),
        runtime_repository=_PagedRuntimeRepository(),
    )

    with pytest.raises(ValidationFailed):
        service.query_runs(run_type="action", cursor="orc1.not-valid-base64")
    with pytest.raises(ValidationFailed):
        service.query_runs(run_type="action", cursor="not-prefixed")
    with pytest.raises(ValidationFailed):
        service.query_runs(limit=501)
    with pytest.raises(ValidationFailed):
        service.query_runs(limit=0)
    with pytest.raises(ValidationFailed):
        service.query_runs(cursor="orc1.not-valid-base64")


def test_runtime_service_query_runs_rejects_cursor_shape_mismatch() -> None:
    cursor = encode_runtime_run_cursor(
        _runtime_row("action_c", "2026-06-10T00:00:02Z"),
        run_type="action",
        status="succeeded",
        since=None,
        until=None,
    )
    service = RuntimeService(
        engine=_UnusedEngine(),
        policy=_UnusedPolicy(),
        runtime_repository=_PagedRuntimeRepository(),
    )

    with pytest.raises(ValidationFailed):
        service.query_runs(run_type="transform", status="succeeded", cursor=cursor)
    with pytest.raises(ValidationFailed):
        service.query_runs(run_type="action", status="failed", cursor=cursor)
    with pytest.raises(ValidationFailed):
        encode_runtime_run_cursor(
            {"id": "run_without_timestamp"},
            run_type="action",
            status=None,
            since=None,
            until=None,
        )


def _runtime_row(run_id: str, created_at: str) -> RuntimeRow:
    return {
        "id": run_id,
        "tenant_id": "tenant-demo",
        "status": "SUCCEEDED",
        "created_at": created_at,
    }
