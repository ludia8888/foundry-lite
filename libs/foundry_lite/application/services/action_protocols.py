"""Application service helpers for action protocols workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from foundry_lite.application.ports import (
    ActionTypeRow,
    InterfaceTypeRow,
    ObjectRecordRow,
    ObjectTypeRow,
    OntologyVersionRow,
    OsdkResourceOperation,
    OsdkResourceType,
    RuntimeRunType,
    TransactionContext,
)
from foundry_lite.application.ports.action_repository import ActionErrorPayload, ObjectProperties
from foundry_lite.domain.context import RequestContext


class ActionObjectIndexer(Protocol):
    def _merge_properties(
        self,
        conn: TransactionContext,
        object_type_id: str,
        base: ObjectProperties,
        edits: ObjectProperties,
    ) -> ObjectProperties: ...


class ActionObjectRecordLookup(Protocol):
    def _object_record(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        object_id: str,
        object_type_id: str | None = None,
    ) -> ObjectRecordRow | None: ...


class ActionOntologyLookup(Protocol):
    def _active_ontology_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
    ) -> OntologyVersionRow: ...

    def _interface_types_for_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
    ) -> Sequence[InterfaceTypeRow]: ...

    def _active_action_type(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        api_name: str,
    ) -> ActionTypeRow: ...

    def _action_type_by_id(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        action_type_id: str,
    ) -> ActionTypeRow: ...

    def _active_object_type(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        api_name: str,
    ) -> ObjectTypeRow: ...


class ActionRuntimeBoundary(Protocol):
    def _require_write_traffic_open(
        self,
        ctx: RequestContext,
        *,
        operation: str,
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

    def _run_relation(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        source_run_type: RuntimeRunType,
        source_run_id: str,
        target_run_type: RuntimeRunType,
        target_run_id: str,
        relation: str,
        resource_type: str,
        resource_id: str,
        metadata: Mapping[str, object] | None = None,
    ) -> bool: ...

    def _error_payload(
        self,
        exc: Exception,
        ctx: RequestContext | None = None,
        *,
        run_id: str | None = None,
        correlation_id: str | None = None,
        adapter: str | None = None,
    ) -> ActionErrorPayload: ...


class ActionOsdkScopeBoundary(Protocol):
    def require_resource_scope(
        self,
        ctx: RequestContext,
        *,
        resource_type: OsdkResourceType,
        resource_api_name: str,
        operation: OsdkResourceOperation,
    ) -> None: ...
