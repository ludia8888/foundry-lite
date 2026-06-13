from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.ports import ActionTypeRow, ObjectRecordRow, TransactionContext
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
    ) -> ObjectRecordRow | None: ...


class ActionOntologyLookup(Protocol):
    def _active_action_type(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        api_name: str,
    ) -> ActionTypeRow: ...


class ActionRuntimeBoundary(Protocol):
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
    ) -> ActionErrorPayload: ...
