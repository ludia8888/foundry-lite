"""Application service helpers for protocols workflows."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.ports import TransactionContext
from foundry_lite.domain.context import RequestContext


class MediaRuntimeBoundary(Protocol):
    """The slice of ``RuntimeService`` the media plane writes through.

    Audit and outbox writes use the same transaction handle as the metadata commit so
    they are atomic with it (a failure here rolls the whole commit back).
    """

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
