"""Mutation helpers for managed Source schedule configuration and pause state."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.ports import SourceManagementRepository, TransactionContext, TransactionManager
from foundry_lite.application.services.source_management_config import (
    resumed_source_schedule,
    source_sync_schedule_fingerprint,
)
from foundry_lite.application.services.source_management_helpers import (
    SourceRuntimeBoundary,
    mapping,
    now,
    require_same_fingerprint,
    sync_row,
)
from foundry_lite.application.services.source_management_views import sync_view
from foundry_lite.application.state_transitions import (
    SOURCE_SYNC_SCHEDULE_PAUSED,
    SOURCE_SYNC_SCHEDULE_RESUMED,
    StatusTransition,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed


class SourceScheduleStore(Protocol):
    engine: TransactionManager
    source_management_repository: SourceManagementRepository


def update_sync_schedule_row(
    service: SourceScheduleStore,
    runtime_service: SourceRuntimeBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    sync_name: str,
    schedule: Mapping[str, object],
    expected_config_fingerprint: str,
    idempotency_key: str,
) -> Mapping[str, object]:
    current = sync_row(service, conn, ctx, sync_name)
    if dict(mapping(current["schedule"])) == dict(schedule):
        return current
    require_same_fingerprint(current, expected_config_fingerprint)
    updated = service.source_management_repository.update_sync_schedule(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        sync_name=sync_name,
        schedule=schedule,
        config_fingerprint=source_sync_schedule_fingerprint(current, schedule),
        updated_at=now(),
    )
    return _audited_update(runtime_service, conn, ctx, sync_name, idempotency_key, current, updated, "updated")


def update_sync_schedule_state_row(
    service: SourceScheduleStore,
    runtime_service: SourceRuntimeBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    sync_name: str,
    target_status: str,
    expected_config_fingerprint: str,
    idempotency_key: str,
    audit_event: str | None = None,
    audit_evidence: Mapping[str, object] | None = None,
) -> Mapping[str, object]:
    current = sync_row(service, conn, ctx, sync_name)
    if current["status"] == target_status:
        return current
    require_same_fingerprint(current, expected_config_fingerprint)
    schedule = _transitioned_schedule(current, target_status)
    transition = _schedule_state_transition(target_status)
    updated = service.source_management_repository.update_sync_schedule_state(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        sync_name=sync_name,
        transition=transition,
        expected_config_fingerprint=expected_config_fingerprint,
        schedule=schedule,
        config_fingerprint=source_sync_schedule_fingerprint(current, schedule, status=target_status),
        updated_at=now(),
    )
    if updated is None:
        return _concurrent_state_result(service, conn, ctx, sync_name, target_status)
    event = audit_event or ("paused" if target_status == "paused" else "resumed")
    return _audited_update(
        runtime_service, conn, ctx, sync_name, idempotency_key, current, updated, event, audit_evidence
    )


def _transitioned_schedule(current: Mapping[str, object], target_status: str) -> Mapping[str, object]:
    if target_status not in {"active", "paused"} or current["status"] not in {"active", "paused"}:
        raise ValidationFailed("unsupported managed sync schedule state transition")
    schedule = mapping(current["schedule"])
    if schedule.get("mode") not in {"interval", "cron"}:
        raise ValidationFailed("only recurring managed sync schedules can be paused")
    return resumed_source_schedule(schedule, now()) if target_status == "active" else dict(schedule)


def _schedule_state_transition(target_status: str) -> StatusTransition:
    return SOURCE_SYNC_SCHEDULE_PAUSED if target_status == "paused" else SOURCE_SYNC_SCHEDULE_RESUMED


def _concurrent_state_result(
    service: SourceScheduleStore,
    conn: TransactionContext,
    ctx: RequestContext,
    sync_name: str,
    target_status: str,
) -> Mapping[str, object]:
    latest = sync_row(service, conn, ctx, sync_name)
    if latest["status"] == target_status:
        return latest
    raise ConflictDetected("source managed sync changed while schedule state was being updated")


def _audited_update(
    runtime_service: SourceRuntimeBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    sync_name: str,
    idempotency_key: str,
    current: Mapping[str, object],
    updated: Mapping[str, object] | None,
    event: str,
    evidence: Mapping[str, object] | None = None,
) -> Mapping[str, object]:
    if updated is None:
        raise NotFound("source managed sync not found", details={"sync_name": sync_name})
    runtime_service._audit(
        conn,
        ctx,
        event_type=f"source.sync.schedule.{event}",
        resource_type="source_sync",
        resource_id=sync_name,
        action="source_manage",
        before_ref=sync_view(current),
        after_ref={**sync_view(updated), **dict(evidence or {})},
        correlation_id=idempotency_key,
    )
    return updated
