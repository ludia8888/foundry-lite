from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, TypedDict

from foundry_lite.application.ports.transaction_context import StatusTransition, TransactionContext


class MaterializationSourceRef(TypedDict, total=False):
    """Source selector for a materialization specification."""

    type: str
    objectType: str


class MaterializationTargetRef(TypedDict):
    """Dataset target selector for a materialization specification."""

    dataset: str


class MaterializationTriggerConfig(TypedDict):
    """Trigger settings for a materialization specification."""

    type: str


class ActionRunWatermark(TypedDict):
    """Last completed action included in an action-log materialization."""

    completed_at: str | None
    action_run_id: str | None


ObjectRecordVersionRow = Mapping[str, object]
ActionLogSourceRow = Mapping[str, object]


class MaterializationRow(TypedDict):
    """Persisted materialization specification row."""

    id: str
    tenant_id: str
    api_name: str
    materialization_type: str
    source_ref: MaterializationSourceRef
    target_ref: MaterializationTargetRef
    trigger_config: MaterializationTriggerConfig
    enabled: bool


@dataclass(frozen=True)
class MaterializationRecord:
    materialization_id: str
    tenant_id: str
    api_name: str
    materialization_type: str
    source_ref: MaterializationSourceRef
    target_ref: MaterializationTargetRef
    trigger_config: MaterializationTriggerConfig
    enabled: bool


@dataclass(frozen=True)
class MaterializationRunRecord:
    materialization_run_id: str
    tenant_id: str
    materialization_id: str
    api_name: str
    status: str
    source_cursor: Mapping[str, object]
    object_store_watermark: Mapping[str, object]
    consistency_level: str
    target_dataset_version_id: str | None
    row_count: int | None
    error: Mapping[str, object] | None
    created_at: str
    completed_at: str | None


@dataclass(frozen=True)
class MaterializationReplayResult:
    materialization_run_id: str
    api_name: str
    watermark: Mapping[str, object]
    row_count: int
    row_hash: str
    rows: Sequence[Mapping[str, object]]


class MaterializationRepository(Protocol):
    """DB boundary for materialization specs, runs, and source watermark reads."""

    def materialization_by_api_name(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        api_name: str,
    ) -> MaterializationRow | None:
        """Return a materialization spec row by tenant + api_name, or None."""
        ...

    def insert_materialization(self, *, transaction: TransactionContext, record: MaterializationRecord) -> None:
        """Persist a newly registered materialization spec."""
        ...

    def materialization_by_id(
        self, *, transaction: TransactionContext, materialization_id: str
    ) -> MaterializationRow | None:
        """Return a materialization spec row by id, or None."""
        ...

    def insert_materialization_run(self, *, transaction: TransactionContext, record: MaterializationRunRecord) -> None:
        """Persist a newly received materialization run."""
        ...

    def update_materialization_run_terminal(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        materialization_run_id: str,
        transition: StatusTransition,
        target_dataset_version_id: str | None,
        row_count: int | None,
        error: Mapping[str, object] | None,
        completed_at: str,
    ) -> bool:
        """CAS a materialization run from an allowed state into a terminal state."""
        ...

    def latest_action_run_watermark(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
    ) -> ActionRunWatermark:
        """Return the latest completed action cursor for a tenant."""
        ...

    def action_log_rows_at_watermark(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        completed_at_lte: str | None,
        action_run_id_lte: str | None,
    ) -> list[ActionLogSourceRow]:
        """Return succeeded action-log source rows bounded by a completed-action cursor."""
        ...

    def latest_object_record_watermark(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_api_name: str,
        index_version: str,
    ) -> int | None:
        """Return the latest object change sequence visible in one active index."""
        ...

    def active_object_index_version(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_api_name: str,
    ) -> str:
        """Return the active object index version for a materialized object type."""
        ...

    def object_records_at_watermark(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        object_type_api_name: str,
        index_version: str,
        max_object_change_sequence: int,
    ) -> list[ObjectRecordVersionRow]:
        """Return object rows as they existed at a fixed object change watermark."""
        ...
