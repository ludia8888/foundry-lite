"""Audited lifecycle transitions for durable Data Connection Sources."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import (
    RuntimeRepository,
    SourceConnectionRow,
    SourceManagementRepository,
    SourceRegistryRepository,
    StatusTransition,
    TransactionContext,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.source_management_config import require_idempotency_key
from foundry_lite.application.services.source_management_helpers import (
    SourceRuntimeBoundary,
    now,
    require_same_fingerprint,
    require_write,
    sync_row,
)
from foundry_lite.application.services.source_onboarding_helpers import source_row_in_tx
from foundry_lite.application.services.source_onboarding_views import source_view
from foundry_lite.application.services.source_streaming_lifecycle import (
    SourceStreamingLifecycleDependencies,
    start_streaming_sync,
    stop_streaming_sync,
    streaming_sync_status,
)
from foundry_lite.application.state_transitions import SOURCE_CONNECTION_DISABLED, SOURCE_CONNECTION_ENABLED
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed


class SourceLifecycleService(CoreService):
    """Own durable Source and managed-stream lifecycle transitions."""

    required_dependencies = (
        "engine",
        "policy",
        "runtime_repository",
        "source_management_repository",
        "source_registry_repository",
    )
    required_collaborators = ("runtime_service",)
    runtime_repository: RuntimeRepository
    source_management_repository: SourceManagementRepository
    source_registry_repository: SourceRegistryRepository
    runtime_service: SourceRuntimeBoundary

    def update_source_status(
        self,
        source_name: str,
        *,
        status: str,
        expected_config_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        require_write(self.policy, self.runtime_service, ctx, "update_source_status", source_name)
        require_idempotency_key(idempotency_key)
        transition = _source_status_transition(status)
        with self.engine.begin() as conn:
            current = source_row_in_tx(self.source_registry_repository, conn, ctx, source_name)
            if current["status"] == status:
                return source_view(current)
            if current["config_fingerprint"] != expected_config_fingerprint:
                raise ConflictDetected("source config changed while status was being updated")
            updated = self.source_registry_repository.update_source_status(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                source_name=source_name,
                transition=transition,
                expected_config_fingerprint=expected_config_fingerprint,
                updated_at=now(),
            )
            if updated is None:
                raise ConflictDetected("source status changed concurrently")
            self._record_status_change(conn, ctx, source_name, status, idempotency_key, current, updated)
        return source_view(updated)

    def start_managed_streaming_sync(
        self,
        sync_name: str,
        *,
        expected_config_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        require_write(self.policy, self.runtime_service, ctx, "start_managed_streaming_sync", sync_name)
        require_idempotency_key(idempotency_key)
        with self.engine.begin() as conn:
            sync = sync_row(self, conn, ctx, sync_name)
            require_same_fingerprint(sync, expected_config_fingerprint)
            self._require_source_active(conn, ctx, sync)
            return start_streaming_sync(self._streaming_dependencies(), conn, ctx, sync, idempotency_key)

    def stop_managed_streaming_sync(
        self,
        sync_name: str,
        *,
        expected_config_fingerprint: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        require_write(self.policy, self.runtime_service, ctx, "stop_managed_streaming_sync", sync_name)
        require_idempotency_key(idempotency_key)
        with self.engine.begin() as conn:
            sync = sync_row(self, conn, ctx, sync_name)
            require_same_fingerprint(sync, expected_config_fingerprint)
            return stop_streaming_sync(self._streaming_dependencies(), conn, ctx, sync)

    def get_managed_streaming_sync_status(
        self, sync_name: str, *, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "source:read")
        with self.engine.begin() as conn:
            sync = sync_row(self, conn, ctx, sync_name)
            return streaming_sync_status(self._streaming_dependencies(), conn, ctx, sync)

    def _streaming_dependencies(self) -> SourceStreamingLifecycleDependencies:
        return SourceStreamingLifecycleDependencies(
            runtime_repository=self.runtime_repository,
            source_management_repository=self.source_management_repository,
            runtime_service=self.runtime_service,
        )

    def _require_source_active(self, conn: TransactionContext, ctx: RequestContext, sync: Mapping[str, object]) -> None:
        source = self.source_registry_repository.source_by_name(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            source_name=str(sync["source_name"]),
        )
        if source is not None and source["status"] == "disabled":
            raise ValidationFailed(
                "source is disabled",
                details={"sourceName": source["source_name"], "syncName": sync["sync_name"]},
            )

    def _record_status_change(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        source_name: str,
        status: str,
        idempotency_key: str,
        current: SourceConnectionRow,
        updated: SourceConnectionRow,
    ) -> None:
        event = "enabled" if status == "active" else "disabled"
        payload = {"sourceName": source_name, "status": status}
        self.runtime_service._audit(
            conn,
            ctx,
            event_type=f"source.{event}",
            resource_type="source",
            resource_id=source_name,
            action="source_manage",
            before_ref=source_view(current),
            after_ref=source_view(updated),
            correlation_id=idempotency_key,
        )
        self.runtime_service._outbox(
            conn,
            ctx,
            f"source.{event}",
            "source",
            source_name,
            payload,
            idempotency_key=idempotency_key,
            correlation_id=idempotency_key,
        )


def _source_status_transition(status: str) -> StatusTransition:
    if status == "active":
        return SOURCE_CONNECTION_ENABLED
    if status == "disabled":
        return SOURCE_CONNECTION_DISABLED
    raise ValidationFailed("source status must be active or disabled", details={"status": status})
