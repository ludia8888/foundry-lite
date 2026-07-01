"""Application port contract for action repository."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import StatusTransition, TransactionContext

ActionParameters = Mapping[str, object]
ActionErrorPayload = Mapping[str, object]
ActionResultPayload = Mapping[str, object]
ActionWritebackPayload = Mapping[str, object]
ObjectProperties = Mapping[str, object]
ObjectPatch = Mapping[str, object]


class ActionRunRow(TypedDict):
    """Persisted action run row returned for idempotent replay checks."""

    id: str
    tenant_id: str
    action_type_id: str
    action_type_api_name: str
    actor_user_id: str
    target_object_type_id: str
    target_object_type_api_name: str
    target_object_id: str
    expected_object_version: int
    parameters: ActionParameters
    status: str
    idempotency_key: str
    request_fingerprint: str
    result: ActionResultPayload | None
    error: ActionErrorPayload | None
    created_at: str
    completed_at: str | None


@dataclass(frozen=True)
class ActionRunRecord:
    action_run_id: str
    tenant_id: str
    action_type_id: str
    action_type_api_name: str
    actor_user_id: str
    target_object_type_id: str
    target_object_type_api_name: str
    target_object_id: str
    expected_object_version: int
    parameters: ActionParameters
    status: str
    idempotency_key: str
    request_fingerprint: str
    result: ActionResultPayload | None
    error: ActionErrorPayload | None
    created_at: str
    completed_at: str | None


@dataclass(frozen=True)
class ActionWritebackRecord:
    writeback_id: str
    tenant_id: str
    action_run_id: str
    mode: str
    connector_id: str
    request: ActionWritebackPayload
    response: ActionWritebackPayload | None
    status: str
    idempotency_key: str
    attempts: int
    created_at: str
    completed_at: str | None


@dataclass(frozen=True)
class ActionWritebackReconciliation:
    writeback_id: str
    tenant_id: str
    action_run_id: str
    response: ActionWritebackPayload
    completed_at: str


@dataclass(frozen=True)
class ObjectTargetUpdate:
    object_record_id: str
    tenant_id: str
    expected_object_version: int
    edit_properties: ObjectProperties
    properties: ObjectProperties
    next_object_version: int
    updated_at: str


@dataclass(frozen=True)
class ObjectEditRecord:
    edit_id: str
    tenant_id: str
    action_run_id: str
    object_type_id: str
    object_type_api_name: str
    object_id: str
    edit_type: str
    patch: ObjectPatch
    previous_values: ObjectProperties
    actor_user_id: str
    idempotency_key: str
    created_at: str


class ActionRepository(Protocol):
    """DB boundary for action runs, writebacks, and object edit persistence."""

    def action_run_by_idempotency(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        action_type_id: str,
        actor_user_id: str,
        idempotency_key: str,
    ) -> ActionRunRow | None:
        """Return an existing action run for an idempotency key."""
        ...

    def action_run_by_id(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        action_run_id: str,
    ) -> ActionRunRow | None:
        """Return one tenant-scoped action run."""
        ...

    def insert_action_run(self, *, transaction: TransactionContext, record: ActionRunRecord) -> None:
        """Persist a newly received action run."""
        ...

    def insert_action_run_or_get_existing(
        self, *, transaction: TransactionContext, record: ActionRunRecord
    ) -> ActionRunRow | None:
        """Persist a new action run, or return the existing run for an idempotency race."""
        ...

    def update_action_run_terminal(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        action_run_id: str,
        transition: StatusTransition,
        error: ActionErrorPayload | None,
        completed_at: str,
        result: ActionResultPayload | None = None,
    ) -> bool:
        """CAS an action run from an allowed state into a terminal state."""
        ...

    def insert_action_writeback(self, *, transaction: TransactionContext, record: ActionWritebackRecord) -> None:
        """Persist one before-commit writeback attempt."""
        ...

    def action_writeback_by_id(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        writeback_id: str,
    ) -> ActionWritebackRecord | None:
        """Return one action writeback row for tenant-scoped Operations actions."""
        ...

    def list_action_writebacks(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        statuses: Sequence[str],
        limit: int,
    ) -> list[ActionWritebackRecord]:
        """Return tenant-scoped writebacks for Operations reconciliation queue views."""
        ...

    def reconcile_action_writeback(
        self,
        *,
        transaction: TransactionContext,
        record: ActionWritebackReconciliation,
    ) -> bool:
        """Mark one unresolved writeback reconciled, returning whether this call won."""
        ...

    def update_object_target(self, *, transaction: TransactionContext, record: ObjectTargetUpdate) -> bool:
        """Apply an optimistic object update and report whether the expected version matched."""
        ...

    def insert_object_edit(self, *, transaction: TransactionContext, record: ObjectEditRecord) -> None:
        """Persist an object edit created by an action run."""
        ...
