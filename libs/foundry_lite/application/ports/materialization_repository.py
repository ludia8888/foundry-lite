from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from foundry_lite.application.ports.transaction_context import TransactionContext


@dataclass(frozen=True)
class MaterializationRecord:
    materialization_id: str
    tenant_id: str
    api_name: str
    materialization_type: str
    source_ref: dict[str, Any]
    target_ref: dict[str, Any]
    trigger_config: dict[str, Any]
    enabled: bool


@dataclass(frozen=True)
class MaterializationRunRecord:
    materialization_run_id: str
    tenant_id: str
    materialization_id: str
    api_name: str
    status: str
    source_cursor: dict[str, Any]
    object_store_watermark: dict[str, Any]
    consistency_level: str
    target_dataset_version_id: str | None
    row_count: int | None
    error: dict[str, Any] | None
    created_at: str
    completed_at: str | None


class MaterializationRepository(Protocol):
    """DB boundary for materialization specs, runs, and source watermark reads."""

    def materialization_by_api_name(
        self,
        *,
        transaction: TransactionContext,
        tenant_id: str,
        api_name: str,
    ) -> dict[str, Any] | None:
        """Return a materialization spec row by tenant + api_name, or None."""
        ...

    def insert_materialization(self, *, transaction: TransactionContext, record: MaterializationRecord) -> None:
        """Persist a newly registered materialization spec."""
        ...

    def materialization_by_id(
        self, *, transaction: TransactionContext, materialization_id: str
    ) -> dict[str, Any] | None:
        """Return a materialization spec row by id, or None."""
        ...

    def insert_materialization_run(self, *, transaction: TransactionContext, record: MaterializationRunRecord) -> None:
        """Persist a newly received materialization run."""
        ...

    def update_materialization_run_terminal(
        self,
        *,
        transaction: TransactionContext,
        materialization_run_id: str,
        status: str,
        target_dataset_version_id: str | None,
        row_count: int | None,
        error: dict[str, Any] | None,
        completed_at: str,
    ) -> None:
        """Mark a materialization run as terminal (succeeded/failed/aborted)."""
        ...

    def latest_action_run_watermark(self, *, transaction: TransactionContext) -> str | None:
        """Return the latest action_runs.created_at watermark (or None if empty)."""
        ...

    def latest_object_record_watermark(self, *, transaction: TransactionContext) -> str | None:
        """Return the latest object_records.updated_at watermark (or None if empty)."""
        ...
