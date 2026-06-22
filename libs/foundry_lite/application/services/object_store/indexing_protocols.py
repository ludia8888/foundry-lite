from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from foundry_lite.application.ports import (
    DatasetRow,
    DatasetVersionRow,
    IndexRunError,
    ObjectRecordRow,
    ObjectTypeRow,
    PropertyTypeRow,
    TransactionContext,
)
from foundry_lite.application.ports.ontology_repository import OntologyVersionRow
from foundry_lite.domain.context import RequestContext


class IndexDatasetRegistry(Protocol):
    def get_dataset(self, dataset_ref: str, *, ctx: RequestContext | None = None) -> DatasetRow: ...


class IndexDatasetTransactionFiles(Protocol):
    def _version_file_path(self, version: DatasetVersionRow) -> Path: ...


class IndexDatasetVersions(Protocol):
    def _latest_version_by_dataset_id(self, conn: TransactionContext, dataset_id: str) -> DatasetVersionRow: ...

    def _version_by_id(self, conn: TransactionContext, version_id: str) -> DatasetVersionRow: ...


class IndexObjectRecords(Protocol):
    def _object_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        object_id: str,
    ) -> ObjectRecordRow | None: ...


class IndexOntologyLookup(Protocol):
    def _active_ontology_version(self, conn: TransactionContext, ctx: RequestContext) -> OntologyVersionRow: ...

    def _active_object_type(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        api_name: str,
    ) -> ObjectTypeRow: ...

    def _properties_for_object_type(
        self,
        conn: TransactionContext,
        object_type_id: str,
    ) -> Sequence[PropertyTypeRow]: ...


class IndexRuntimeBoundary(Protocol):
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
    ) -> None: ...

    def _error_payload(
        self,
        exc: Exception,
        ctx: RequestContext | None = None,
        *,
        run_id: str | None = None,
        correlation_id: str | None = None,
        adapter: str | None = None,
    ) -> IndexRunError: ...
