"""Application service helpers for osdk application scope workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports import (
    OsdkApplicationBundle,
    OsdkApplicationClientRecord,
    OsdkApplicationRepository,
    OsdkApplicationResourceRow,
    OsdkApplicationRow,
    OsdkResourceOperation,
    OsdkResourceType,
    OsdkSdkCompatibilityWindowRow,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.osdk_application_artifacts import _is_expired, _snapshot_scopes
from foundry_lite.application.services.osdk_application_records import (
    _application_record,
    _default_client_id,
    _raise_scope_denied,
    _require_idempotency_key,
    _resource_record,
    _scope_allowed,
    scope_for,
)
from foundry_lite.application.services.osdk_application_types import OsdkRuntimeAuditBoundary
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, PermissionDenied
from foundry_lite.security.policy import PolicyService


class OsdkApplicationScopeMixin:
    engine: TransactionManager
    osdk_application_repository: OsdkApplicationRepository
    policy: PolicyService
    runtime_service: OsdkRuntimeAuditBoundary

    def create_application(
        self,
        *,
        ctx: RequestContext | None = None,
        app_api_name: str,
        display_name: str,
        client_id: str | None = None,
        resources: Sequence[Mapping[str, object]] = (),
        idempotency_key: str,
    ) -> OsdkApplicationBundle:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "developer_console:manage")
        _require_idempotency_key(idempotency_key)
        now = _now()
        record = _application_record(ctx, app_api_name, display_name, idempotency_key, now)
        with self.engine.begin() as conn:
            existing = self.osdk_application_repository.insert_application_or_get_existing(
                transaction=conn, record=record
            )
            row = existing or self.osdk_application_repository.application_by_id(
                transaction=conn, tenant_id=ctx.tenant_id, app_id=record.application_id
            )
            if row is None:
                raise RuntimeError("created OSDK application could not be read back")
            self._ensure_client(conn, ctx, row["id"], client_id or _default_client_id(app_api_name), now)
            if existing is None:
                self._replace_resources(conn, ctx, row["id"], resources, now)
                self._audit(conn, ctx, "osdk.application.created", row)
            return self._application_bundle(conn, ctx, row["id"])

    def list_applications(self, *, ctx: RequestContext | None = None) -> list[OsdkApplicationBundle]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "developer_console:read")
        with self.engine.begin() as conn:
            rows = self.osdk_application_repository.list_applications(transaction=conn, tenant_id=ctx.tenant_id)
            return [self._application_bundle(conn, ctx, row["id"]) for row in rows]

    def get_application(self, app_id: str, *, ctx: RequestContext | None = None) -> OsdkApplicationBundle:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "developer_console:read")
        with self.engine.begin() as conn:
            self._require_application(conn, ctx, app_id)
            return self._application_bundle(conn, ctx, app_id)

    def update_resources(
        self,
        app_id: str,
        *,
        ctx: RequestContext | None = None,
        resources: Sequence[Mapping[str, object]],
        idempotency_key: str,
    ) -> OsdkApplicationBundle:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "developer_console:manage")
        _require_idempotency_key(idempotency_key)
        with self.engine.begin() as conn:
            before = self._application_bundle(conn, ctx, app_id)
            self._replace_resources(conn, ctx, app_id, resources, _now())
            after = self._application_bundle(conn, ctx, app_id)
            self._audit(conn, ctx, "osdk.application.resources_updated", after["application"], before, after)
            return after

    def require_resource_scope(
        self,
        ctx: RequestContext,
        *,
        resource_type: OsdkResourceType,
        resource_api_name: str,
        operation: OsdkResourceOperation,
    ) -> None:
        if ctx.application_id is None and not ctx.token_scopes:
            return
        expected_scope = scope_for(resource_type, resource_api_name, operation)
        with self.engine.begin() as conn:
            app = self._require_active_application(conn, ctx)
            app_id = cast(str, app["id"])
            self._require_active_client(conn, ctx, app_id)
            resources = self.osdk_application_repository.resources_for_application(
                transaction=conn, tenant_id=ctx.tenant_id, app_id=app_id
            )
            has_deprecated_grant = self._deprecated_scope_allowed(conn, ctx, app_id, expected_scope)
        if not _scope_allowed(expected_scope, ctx.token_scopes, resources) and not has_deprecated_grant:
            _raise_scope_denied(expected_scope, resource_type, resource_api_name)

    def _application_bundle(self, conn: TransactionContext, ctx: RequestContext, app_id: str) -> OsdkApplicationBundle:
        app = self._require_application(conn, ctx, app_id)
        clients = self.osdk_application_repository.clients_for_application(
            transaction=conn, tenant_id=ctx.tenant_id, app_id=app_id
        )
        return {"application": app, "clients": clients, "resources": self._resources(conn, ctx, app_id)}

    def _resources(
        self, conn: TransactionContext, ctx: RequestContext, app_id: str
    ) -> list[OsdkApplicationResourceRow]:
        return self.osdk_application_repository.resources_for_application(
            transaction=conn, tenant_id=ctx.tenant_id, app_id=app_id
        )

    def _require_application(self, conn: TransactionContext, ctx: RequestContext, app_id: str) -> OsdkApplicationRow:
        row = self.osdk_application_repository.application_by_id(
            transaction=conn, tenant_id=ctx.tenant_id, app_id=app_id
        )
        if row is None:
            raise NotFound("OSDK application not found", details={"appId": app_id})
        return row

    def _require_active_application(self, conn: TransactionContext, ctx: RequestContext) -> Mapping[str, object]:
        if ctx.application_id is None:
            raise PermissionDenied("OSDK application id is required for scoped tokens")
        row = self.osdk_application_repository.application_by_id(
            transaction=conn, tenant_id=ctx.tenant_id, app_id=ctx.application_id
        )
        if row is None:
            raise PermissionDenied("OSDK application is not active", details={"appId": ctx.application_id})
        if row["status"] != "active":
            raise PermissionDenied("OSDK application is inactive", details={"appId": ctx.application_id})
        return row

    def _require_active_client(self, conn: TransactionContext, ctx: RequestContext, app_id: str) -> None:
        if ctx.client_id is None:
            raise PermissionDenied("OSDK client id is required for scoped tokens")
        row = self.osdk_application_repository.active_client(
            transaction=conn, tenant_id=ctx.tenant_id, client_id=ctx.client_id
        )
        if row is None or row["app_id"] != app_id:
            raise PermissionDenied("OSDK client is not active for application")

    def _deprecated_scope_allowed(
        self, conn: TransactionContext, ctx: RequestContext, app_id: str, expected_scope: str
    ) -> bool:
        if expected_scope not in ctx.token_scopes:
            return False
        now = _now()
        windows = self.osdk_application_repository.compatibility_windows_for_application(
            transaction=conn, tenant_id=ctx.tenant_id, app_id=app_id
        )
        return any(self._window_contains_deprecated_scope(conn, ctx, window, expected_scope, now) for window in windows)

    def _window_contains_deprecated_scope(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        window: OsdkSdkCompatibilityWindowRow,
        expected_scope: str,
        now: str,
    ) -> bool:
        if window["status"] != "active" or _is_expired(window["supported_until"], now):
            return False
        old_row = self.osdk_application_repository.sdk_version_by_id(
            transaction=conn, tenant_id=ctx.tenant_id, version_id=window["from_sdk_version_id"]
        )
        new_row = self.osdk_application_repository.sdk_version_by_id(
            transaction=conn, tenant_id=ctx.tenant_id, version_id=window["to_sdk_version_id"]
        )
        if old_row is None or new_row is None:
            return False
        deprecated_scopes = _snapshot_scopes(old_row["resource_scope_snapshot"]) - _snapshot_scopes(
            new_row["resource_scope_snapshot"]
        )
        return any(scope == expected_scope for _resource_type, _resource_name, scope in deprecated_scopes)

    def _replace_resources(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        app_id: str,
        resources: Sequence[Mapping[str, object]],
        now: str,
    ) -> None:
        self._require_application(conn, ctx, app_id)
        records = tuple(_resource_record(ctx.tenant_id, app_id, resource, now) for resource in resources)
        self.osdk_application_repository.replace_resources(
            transaction=conn, tenant_id=ctx.tenant_id, app_id=app_id, resources=records
        )

    def _ensure_client(
        self, conn: TransactionContext, ctx: RequestContext, app_id: str, client_id: str, now: str
    ) -> None:
        self.osdk_application_repository.insert_client_or_get_existing(
            transaction=conn,
            record=OsdkApplicationClientRecord(
                client_row_id=_new_id("osdk_client"),
                tenant_id=ctx.tenant_id,
                app_id=app_id,
                client_id=client_id,
                status="active",
                created_at=now,
            ),
        )

    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event_type: str,
        row: Mapping[str, object],
        before_ref: Mapping[str, object] | None = None,
        after_ref: Mapping[str, object] | None = None,
    ) -> None:
        self.runtime_service._audit(
            conn,
            ctx,
            event_type=event_type,
            resource_type="osdk_application",
            resource_id=cast(str, row["id"]),
            action=event_type,
            before_ref=before_ref,
            after_ref=after_ref or row,
        )
