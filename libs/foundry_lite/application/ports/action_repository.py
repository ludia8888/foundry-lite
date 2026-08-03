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


class ActionRunUsageRow(TypedDict):
    """Aggregated action-run activity for one ontology usage window.

    Usage read models need counts and recency, not row payloads, so the
    aggregation lives at the port where each adapter can push it into SQL.
    """

    status_counts: dict[str, int]
    total_runs: int
    distinct_actor_count: int
    last_run_at: str | None


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
    external_writeback_uri: str | None
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
    external_writeback_uri: str | None = None


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


class ObjectEditRow(TypedDict):
    """One persisted object/link edit row from the unified action edit log."""

    id: str
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


@dataclass(frozen=True)
class ObjectCreateWrite:
    """Insert a brand-new object record originating from an action edit.

    An action-created object has no dataset source: its properties are entirely
    edit-owned, so ``base_properties`` starts empty and every property is version
    1. Keyed by business identity (tenant/type/object id) under ``index_version``.
    """

    object_record_id: str
    tenant_id: str
    object_type_id: str
    object_type_api_name: str
    object_id: str
    properties: ObjectProperties
    created_at: str


@dataclass(frozen=True)
class ObjectDeleteWrite:
    """Soft-delete an existing object under optimistic concurrency.

    Keyed on the surrogate ``object_record_id`` and expected version, mirroring
    ``ObjectTargetUpdate``: the committer resolves the target row (which carries
    its surrogate id) inside the commit transaction, then CASes the deletion.
    """

    object_record_id: str
    tenant_id: str
    expected_object_version: int
    deletion_reason: str
    updated_at: str


@dataclass(frozen=True)
class ObjectLinkWrite:
    """Create (or reactivate a soft-deleted) many-to-many link between two objects."""

    link_record_id: str
    tenant_id: str
    link_type_id: str
    link_type_api_name: str
    from_object_type_id: str
    from_api_name: str
    from_object_id: str
    to_object_type_id: str
    to_api_name: str
    to_object_id: str
    updated_at: str


@dataclass(frozen=True)
class ObjectLinkDeleteWrite:
    """Soft-delete a many-to-many link between two objects."""

    tenant_id: str
    link_type_id: str
    from_object_id: str
    to_object_id: str
    deletion_reason: str
    updated_at: str


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

    def list_action_runs_by_status(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        statuses: Sequence[str],
        limit: int,
    ) -> list[ActionRunRow]:
        """Return tenant-scoped action runs in the given statuses (for the external-pending recovery sweep)."""
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
        completed_at: str | None,
        result: ActionResultPayload | None = None,
    ) -> bool:
        """CAS an action run across a status transition (``completed_at`` is ``None`` for a non-terminal move)."""
        ...

    def action_run_usage(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        since: str,
        action_type_api_name: str | None = None,
        target_object_type_api_name: str | None = None,
    ) -> ActionRunUsageRow:
        """Aggregate tenant action runs created at or after ``since``.

        One method serves both ontology usage read models: the action-type
        view filters by ``action_type_api_name`` and the object-type view
        filters by ``target_object_type_api_name``. Filters AND together.
        """
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

    def create_object_record(self, *, transaction: TransactionContext, record: ObjectCreateWrite) -> bool:
        """Insert an action-created object; return False if its identity already exists."""
        ...

    def soft_delete_object_target(self, *, transaction: TransactionContext, record: ObjectDeleteWrite) -> bool:
        """Soft-delete an object under optimistic concurrency; report whether the expected version matched."""
        ...

    def create_object_link(self, *, transaction: TransactionContext, record: ObjectLinkWrite) -> None:
        """Create or reactivate a many-to-many link between two objects (idempotent on link identity)."""
        ...

    def soft_delete_object_link(self, *, transaction: TransactionContext, record: ObjectLinkDeleteWrite) -> bool:
        """Soft-delete a link; report whether an active link existed to delete."""
        ...

    def insert_object_edit(self, *, transaction: TransactionContext, record: ObjectEditRecord) -> None:
        """Persist an object edit created by an action run."""
        ...

    def object_edits_for_run(
        self, *, transaction: TransactionContext, tenant_id: str, action_run_id: str
    ) -> list[ObjectEditRow]:
        """Return one action run's edits in application order (for the action log and revert)."""
        ...

    def latest_object_edit(
        self, *, transaction: TransactionContext, tenant_id: str, object_type_id: str, object_id: str
    ) -> ObjectEditRow | None:
        """Return the most recent edit touching one object, to detect a superseding edit before revert."""
        ...
