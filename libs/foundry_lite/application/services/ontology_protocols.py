"""Application service helpers for ontology protocols workflows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.ports import (
    DatasetRow,
    DatasetSchemaRow,
    DatasetVersionRow,
    TransactionContext,
)
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.domain.context import RequestContext


class OntologyDatasetRegistry(Protocol):
    def get_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> DatasetRow: ...


class OntologyDatasetVersions(Protocol):
    def _latest_version_by_dataset_id(
        self,
        conn: TransactionContext,
        dataset_id: str,
    ) -> DatasetVersionRow: ...

    def _schema_for_version(self, dataset_id: str, schema_version: int) -> DatasetSchemaRow: ...


class OntologyRuntimeBoundary(RuntimeEvidenceBoundary, Protocol):
    def _require_write_traffic_open(
        self,
        ctx: RequestContext,
        *,
        operation: str,
        resource_type: str,
        resource_id: str,
    ) -> None: ...

    def _require_or_audit(
        self,
        ctx: RequestContext,
        permission: str,
        resource_type: str,
        resource_id: str,
    ) -> None: ...

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

    def _outbox(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> str | None: ...


def require_ontology_write_open(
    runtime_service: OntologyRuntimeBoundary,
    ctx: RequestContext,
    operation: str,
    resource_type: str,
    resource_id: str,
) -> None:
    runtime_service._require_write_traffic_open(
        ctx,
        operation=operation,
        resource_type=resource_type,
        resource_id=resource_id,
    )
