from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from foundry_lite.application.ports.transaction_context import TransactionContext

RuntimeLookupTable = Literal["transforms", "materializations"]
RuntimeRowsTable = Literal[
    "sync_runs",
    "transform_runs",
    "index_runs",
    "action_runs",
    "action_writebacks",
    "materialization_runs",
    "outbox_events",
    "audit_events",
    "object_edits",
    "object_records",
]


@dataclass(frozen=True)
class AuditEventRecord:
    event_id: str
    tenant_id: str
    actor_user_id: str
    event_type: str
    resource_type: str
    resource_id: str | None
    action: str
    decision: str
    policy_decision: dict[str, Any]
    before_ref: dict[str, Any]
    after_ref: dict[str, Any]
    correlation_id: str
    request_id: str
    metadata: dict[str, Any]
    created_at: str


@dataclass(frozen=True)
class OutboxEventRecord:
    event_id: str
    tenant_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: dict[str, Any]
    status: str
    attempts: int
    idempotency_key: str
    correlation_id: str
    created_at: str
    published_at: str | None


@dataclass(frozen=True)
class LineageEdgeRecord:
    edge_id: str
    tenant_id: str
    from_resource_type: str
    from_resource_id: str
    to_resource_type: str
    to_resource_id: str
    relation: str
    created_by_run_id: str
    created_at: str


class RuntimeRepository(Protocol):
    """DB boundary for runtime audit, outbox, lineage, and operational reads."""

    def lineage_for_resource(self, *, tenant_id: str, resource_id: str) -> list[dict[str, Any]]:
        """Return lineage edges touching a resource for one tenant."""
        ...

    def list_runs(self, *, tenant_id: str) -> dict[str, list[dict[str, Any]]]:
        """Return operational run, audit, and outbox rows for one tenant."""
        ...

    def row_by_id(
        self, *, transaction: TransactionContext, table: RuntimeLookupTable, row_id: str
    ) -> dict[str, Any] | None:
        """Return a row by id from a small allowlisted runtime lookup table."""
        ...

    def rows_for_tenant(
        self, *, transaction: TransactionContext, table: RuntimeRowsTable, tenant_id: str
    ) -> list[dict[str, Any]]:
        """Return rows by tenant from an allowlisted runtime table."""
        ...

    def insert_audit_event(self, *, transaction: TransactionContext, record: AuditEventRecord) -> None:
        """Persist an audit event inside the caller transaction."""
        ...

    def insert_outbox_event(self, *, transaction: TransactionContext, record: OutboxEventRecord) -> bool:
        """Persist an outbox event, returning False for idempotent duplicates."""
        ...

    def insert_lineage_edge(self, *, transaction: TransactionContext, record: LineageEdgeRecord) -> None:
        """Persist a lineage edge inside the caller transaction."""
        ...
