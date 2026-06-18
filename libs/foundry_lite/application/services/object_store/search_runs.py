from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, cast

from foundry_lite.application.ports import (
    IndexRunError,
    IndexRunRecord,
    IndexRunSourceRef,
    ObjectTypeRow,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.ports.object_index_repository import ObjectIndexRepository
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.domain.context import RequestContext


class SearchRunRuntimeBoundary(Protocol):
    """Runtime evidence collaborator used by search projection run tracking."""

    def _error_payload(
        self,
        exc: Exception,
        ctx: RequestContext | None = None,
        *,
        run_id: str | None = None,
        correlation_id: str | None = None,
        adapter: str | None = None,
    ) -> Mapping[str, object]: ...

    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str | None,
        action: str,
        decision: str = "allow",
        policy_decision: Mapping[str, object] | None = None,
        before_ref: Mapping[str, object] | None = None,
        after_ref: Mapping[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> None: ...


def start_search_index_run(
    engine: TransactionManager,
    repository: ObjectIndexRepository,
    adapter_profile: str,
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    object_type_api_name: str,
    trigger_type: str,
    object_id: str | None = None,
) -> str:
    """Persist the running search projection operation before adapter I/O."""

    run_id = _new_id("index_run")
    record = _search_index_run_record(
        ctx,
        object_type,
        object_type_api_name,
        trigger_type,
        adapter_profile,
        run_id,
        object_id,
    )
    with engine.begin() as conn:
        repository.create_index_run(transaction=conn, record=record)
    return run_id


def succeed_search_rebuild(
    engine: TransactionManager,
    repository: ObjectIndexRepository,
    ctx: RequestContext,
    run_id: str,
    result: Mapping[str, object],
) -> None:
    object_count = _int_result(result, "objectCount")
    with engine.begin() as conn:
        repository.mark_index_run_succeeded(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            run_id=run_id,
            rows_read=object_count,
            objects_upserted=object_count,
            objects_deleted=0,
            links_upserted=0,
            cursor={"last_row": object_count},
            completed_at=_now(),
        )


def succeed_search_object_change(
    engine: TransactionManager,
    repository: ObjectIndexRepository,
    ctx: RequestContext,
    run_id: str,
    result: Mapping[str, object],
) -> None:
    status = str(result.get("status", ""))
    with engine.begin() as conn:
        repository.mark_index_run_succeeded(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            run_id=run_id,
            rows_read=1,
            objects_upserted=1 if status == "indexed" else 0,
            objects_deleted=1 if status == "deleted" else 0,
            links_upserted=0,
            cursor={"last_row": 1},
            completed_at=_now(),
        )


def fail_search_index_run(
    engine: TransactionManager,
    repository: ObjectIndexRepository,
    runtime_service: SearchRunRuntimeBoundary,
    adapter_profile: str,
    ctx: RequestContext,
    run_id: str,
    resource_id: str,
    action: str,
    exc: Exception,
) -> None:
    """Persist search adapter failure where operations can inspect it later."""

    error = runtime_service._error_payload(
        exc,
        ctx,
        run_id=run_id,
        correlation_id=run_id,
        adapter=adapter_profile,
    )
    with engine.begin() as conn:
        repository.mark_index_run_failed(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            run_id=run_id,
            error=cast(IndexRunError, dict(error)),
            completed_at=_now(),
        )
        audit_search_failure(runtime_service, conn, ctx, resource_id, action, run_id, error)


def audit_search_failure(
    runtime_service: SearchRunRuntimeBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    resource_id: str,
    action: str,
    run_id: str,
    error: Mapping[str, object],
) -> None:
    runtime_service._audit(
        conn,
        ctx,
        event_type="object.search.failed",
        resource_type="search_index",
        resource_id=resource_id,
        action=action,
        decision="error",
        after_ref={"indexRunId": run_id, "error": dict(error)},
        correlation_id=run_id,
    )


def _search_run_source_ref(
    ctx: RequestContext,
    object_type_api_name: str,
    adapter_profile: str,
    object_id: str | None,
) -> IndexRunSourceRef:
    resource_id = object_type_api_name if object_id is None else f"{object_type_api_name}/{object_id}"
    source: IndexRunSourceRef = {
        "adapter_profile": adapter_profile,
        "mode": "search_projection",
        "request_id": ctx.request_id,
        "resource_id": resource_id,
    }
    if object_id is not None:
        source["object_id"] = object_id
    return source


def _search_index_run_record(
    ctx: RequestContext,
    object_type: ObjectTypeRow,
    object_type_api_name: str,
    trigger_type: str,
    adapter_profile: str,
    run_id: str,
    object_id: str | None,
) -> IndexRunRecord:
    now = _now()
    return IndexRunRecord(
        run_id=run_id,
        tenant_id=ctx.tenant_id,
        object_type_id=object_type["id"],
        object_type_api_name=object_type_api_name,
        trigger_type=trigger_type,
        source_ref=_search_run_source_ref(ctx, object_type_api_name, adapter_profile, object_id),
        status="running",
        cursor={},
        rows_read=0,
        objects_upserted=0,
        objects_deleted=0,
        links_upserted=0,
        error=None,
        started_at=now,
        completed_at=None,
        created_at=now,
    )


def _int_result(result: Mapping[str, object], key: str) -> int:
    value = result.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0
