"""Application port contract for durable Pipeline schedule state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import TransactionContext

JsonObject = dict[str, object]


class PipelineScheduleRow(TypedDict):
    id: str
    tenant_id: str
    pipeline_id: str
    version_id: str
    schedule: JsonObject
    enabled: bool
    status: str
    updated_by: str
    updated_at: str
    last_tick_at: str | None
    last_slot_at: str | None
    trigger_type: str | None
    timezone: str | None
    next_due_at: str | None
    runtime_config_updated_at: str | None
    lease_owner: str | None
    lease_token: str | None
    lease_expires_at: str | None
    fencing_token: int
    failure_count: int
    paused_reason: str | None
    last_failure_at: str | None
    last_error: JsonObject | None


class PipelineScheduleOperationRow(TypedDict):
    id: str
    tenant_id: str
    pipeline_id: str
    operation: str
    idempotency_key: str
    request_fingerprint: str
    result: JsonObject | None
    created_by: str
    created_at: str


@dataclass(frozen=True)
class PipelineScheduleRecord:
    schedule_id: str
    tenant_id: str
    pipeline_id: str
    version_id: str
    schedule: JsonObject
    enabled: bool
    status: str
    trigger_type: str
    timezone: str
    next_due_at: str | None
    updated_by: str
    updated_at: str
    paused_reason: str | None = None


@dataclass(frozen=True)
class PipelineScheduleOperationRecord:
    operation_id: str
    tenant_id: str
    pipeline_id: str
    operation: str
    idempotency_key: str
    request_fingerprint: str
    created_by: str
    created_at: str


class PipelineScheduleRepository(Protocol):
    """DB boundary for tenant-scoped Pipeline scheduler resources."""

    def upsert_schedule(
        self, *, transaction: TransactionContext, record: PipelineScheduleRecord
    ) -> PipelineScheduleRow: ...

    def schedule_by_pipeline(
        self, *, transaction: TransactionContext, tenant_id: str, pipeline_id: str
    ) -> PipelineScheduleRow | None: ...

    def delete_schedule(self, *, transaction: TransactionContext, tenant_id: str, pipeline_id: str) -> bool: ...

    def list_due_schedules(
        self, *, transaction: TransactionContext, tenant_id: str, due_at: str, limit: int
    ) -> list[PipelineScheduleRow]: ...

    def list_schedules_needing_reconciliation(
        self, *, transaction: TransactionContext, tenant_id: str, observed_at: str, limit: int
    ) -> list[PipelineScheduleRow]: ...

    def reconcile_schedule_runtime(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        schedule_id: str,
        expected_updated_at: str,
        observed_at: str,
        schedule: JsonObject,
        status: str,
        trigger_type: str,
        timezone: str,
        next_due_at: str | None,
        paused_reason: str | None,
    ) -> PipelineScheduleRow | None: ...

    def claim_due_schedule(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        schedule_id: str,
        due_at: str,
        lease_owner: str,
        lease_token: str,
        lease_expires_at: str,
    ) -> PipelineScheduleRow | None: ...

    def complete_schedule_tick(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        schedule_id: str,
        lease_token: str,
        fencing_token: int,
        values: JsonObject,
    ) -> PipelineScheduleRow | None: ...

    def update_schedule_status(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        pipeline_id: str,
        status: str,
        enabled: bool,
        next_due_at: str | None,
        paused_reason: str | None,
        updated_by: str,
        updated_at: str,
    ) -> PipelineScheduleRow | None: ...

    def reserve_schedule_operation(
        self,
        *,
        transaction: TransactionContext,
        record: PipelineScheduleOperationRecord,
    ) -> tuple[PipelineScheduleOperationRow, bool]: ...

    def complete_schedule_operation(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        operation_id: str,
        result: JsonObject,
    ) -> PipelineScheduleOperationRow | None: ...
