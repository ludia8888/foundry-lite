"""Application service helpers for osdk application clients workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, cast

from foundry_lite.application.ports import (
    OsdkApplicationClientRow,
    OsdkApplicationRepository,
    RuntimeJsonObject,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.osdk_application_idempotency import OsdkApplicationIdempotencyService
from foundry_lite.application.services.osdk_application_records import (
    _clean_strings,
    _client_record,
    _client_request,
    _client_update_request,
    _positive_ttl,
    _require_idempotency_key,
    _string_sequence,
    _ttl,
)
from foundry_lite.application.services.osdk_application_scope import OsdkApplicationScopeService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed
from foundry_lite.security.policy import PolicyService


class _AuditCallable(Protocol):
    def __call__(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event_type: str,
        row: Mapping[str, object],
        before_ref: Mapping[str, object] | None = None,
        after_ref: Mapping[str, object] | None = None,
    ) -> None: ...


class OsdkApplicationClientService(CoreService):
    required_dependencies = ("engine", "policy", "osdk_application_repository")
    required_collaborators = ("osdk_application_scope_service", "osdk_application_idempotency_service")

    engine: TransactionManager
    osdk_application_repository: OsdkApplicationRepository
    policy: PolicyService
    osdk_application_scope_service: OsdkApplicationScopeService
    osdk_application_idempotency_service: OsdkApplicationIdempotencyService

    def create_client(
        self,
        app_id: str,
        *,
        ctx: RequestContext | None = None,
        client_id: str,
        redirect_uris: Sequence[str],
        allowed_scopes: Sequence[str],
        access_token_ttl_seconds: int = 900,
        refresh_token_ttl_seconds: int = 2_592_000,
        idempotency_key: str,
    ) -> OsdkApplicationClientRow:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "developer_console:manage")
        _require_idempotency_key(idempotency_key)
        token_ttls = (access_token_ttl_seconds, refresh_token_ttl_seconds)
        request = _client_request(app_id, client_id, redirect_uris, allowed_scopes, *token_ttls)
        with self.engine.begin() as conn:
            response = self.osdk_application_idempotency_service._idempotent_response(
                conn,
                ctx,
                "osdk.application.client.create",
                idempotency_key,
                request,
                lambda: self._create_client_json(
                    conn, ctx, app_id, client_id, redirect_uris, allowed_scopes, *token_ttls
                ),
            )
            return cast(OsdkApplicationClientRow, response)

    def list_clients(self, app_id: str, *, ctx: RequestContext | None = None) -> list[OsdkApplicationClientRow]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "developer_console:read")
        with self.engine.begin() as conn:
            self.osdk_application_scope_service._require_application(conn, ctx, app_id)
            return self.osdk_application_repository.clients_for_application(
                transaction=conn, tenant_id=ctx.tenant_id, app_id=app_id
            )

    def update_client(
        self,
        app_id: str,
        client_row_id: str,
        *,
        ctx: RequestContext | None = None,
        status: str,
        redirect_uris: Sequence[str],
        allowed_scopes: Sequence[str],
        access_token_ttl_seconds: int = 900,
        refresh_token_ttl_seconds: int = 2_592_000,
        idempotency_key: str,
    ) -> OsdkApplicationClientRow:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "developer_console:manage")
        _require_idempotency_key(idempotency_key)
        token_ttls = (access_token_ttl_seconds, refresh_token_ttl_seconds)
        request = _client_update_request(app_id, client_row_id, status, redirect_uris, allowed_scopes, *token_ttls)
        with self.engine.begin() as conn:
            response = self.osdk_application_idempotency_service._idempotent_response(
                conn,
                ctx,
                "osdk.application.client.update",
                idempotency_key,
                request,
                lambda: self._update_client_json(
                    conn, ctx, app_id, client_row_id, status, redirect_uris, allowed_scopes, *token_ttls
                ),
            )
            return cast(OsdkApplicationClientRow, response)

    def deactivate_client(
        self, app_id: str, client_row_id: str, *, ctx: RequestContext | None = None, idempotency_key: str
    ) -> OsdkApplicationClientRow:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "developer_console:manage")
        _require_idempotency_key(idempotency_key)
        request = {"appId": app_id, "clientRowId": client_row_id}
        with self.engine.begin() as conn:
            response = self.osdk_application_idempotency_service._idempotent_response(
                conn,
                ctx,
                "osdk.application.client.deactivate",
                idempotency_key,
                request,
                lambda: self._deactivate_client_json(conn, ctx, app_id, client_row_id),
            )
            return cast(OsdkApplicationClientRow, response)

    def _create_client_json(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        app_id: str,
        client_id: str,
        redirect_uris: Sequence[str],
        allowed_scopes: Sequence[str],
        access_token_ttl_seconds: int,
        refresh_token_ttl_seconds: int,
    ) -> RuntimeJsonObject:
        self.osdk_application_scope_service._require_application(conn, ctx, app_id)
        record = _client_record(ctx, app_id, client_id, _now())
        existing = self.osdk_application_repository.insert_client_or_get_existing(transaction=conn, record=record)
        row = existing or self.osdk_application_repository.active_client(
            transaction=conn, tenant_id=ctx.tenant_id, client_id=client_id
        )
        if row is None:
            raise RuntimeError("created OSDK client could not be read back")
        updated = self._update_client_row(
            conn, ctx, row, "active", redirect_uris, allowed_scopes, access_token_ttl_seconds, refresh_token_ttl_seconds
        )
        self.osdk_application_scope_service._audit(conn, ctx, "osdk.application.client_created", updated)
        return cast(RuntimeJsonObject, updated)

    def _update_client_json(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        app_id: str,
        client_row_id: str,
        status: str,
        redirect_uris: Sequence[str],
        allowed_scopes: Sequence[str],
        access_token_ttl_seconds: int,
        refresh_token_ttl_seconds: int,
    ) -> RuntimeJsonObject:
        row = self._require_client_row(conn, ctx, app_id, client_row_id)
        updated = self._update_client_row(
            conn, ctx, row, status, redirect_uris, allowed_scopes, access_token_ttl_seconds, refresh_token_ttl_seconds
        )
        self.osdk_application_scope_service._audit(conn, ctx, "osdk.application.client_updated", updated, row, updated)
        return cast(RuntimeJsonObject, updated)

    def _deactivate_client_json(
        self, conn: TransactionContext, ctx: RequestContext, app_id: str, client_row_id: str
    ) -> RuntimeJsonObject:
        row = self._require_client_row(conn, ctx, app_id, client_row_id)
        updated = self._update_client_row(
            conn,
            ctx,
            row,
            "inactive",
            _string_sequence(row.get("redirect_uris")),
            _string_sequence(row.get("allowed_scopes")),
            _ttl(row.get("access_token_ttl_seconds"), 900),
            _ttl(row.get("refresh_token_ttl_seconds"), 2_592_000),
        )
        self.osdk_application_scope_service._audit(
            conn, ctx, "osdk.application.client_deactivated", updated, row, updated
        )
        return cast(RuntimeJsonObject, updated)

    def _require_client_row(
        self, conn: TransactionContext, ctx: RequestContext, app_id: str, client_row_id: str
    ) -> OsdkApplicationClientRow:
        self.osdk_application_scope_service._require_application(conn, ctx, app_id)
        for row in self.osdk_application_repository.clients_for_application(
            transaction=conn, tenant_id=ctx.tenant_id, app_id=app_id
        ):
            if row["id"] == client_row_id:
                return row
        raise NotFound("OSDK application client not found", details={"appId": app_id, "clientId": client_row_id})

    def _update_client_row(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        row: OsdkApplicationClientRow,
        status: str,
        redirect_uris: Sequence[str],
        allowed_scopes: Sequence[str],
        access_token_ttl_seconds: int,
        refresh_token_ttl_seconds: int,
    ) -> OsdkApplicationClientRow:
        if status not in {"active", "inactive"}:
            raise ValidationFailed("OSDK application client status must be active or inactive")
        scopes = self._validated_client_scopes(conn, ctx, row["app_id"], allowed_scopes)
        updated = self.osdk_application_repository.update_client(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            client_row_id=row["id"],
            status=status,
            redirect_uris=tuple(_clean_strings(redirect_uris)),
            allowed_scopes=scopes,
            access_token_ttl_seconds=_positive_ttl(access_token_ttl_seconds),
            refresh_token_ttl_seconds=_positive_ttl(refresh_token_ttl_seconds),
            updated_at=_now(),
        )
        if updated is None:
            raise RuntimeError("updated OSDK application client could not be read back")
        return updated

    def _validated_client_scopes(
        self, conn: TransactionContext, ctx: RequestContext, app_id: str, scopes: Sequence[str]
    ) -> tuple[str, ...]:
        requested = tuple(_clean_strings(scopes))
        granted = {
            str(scope)
            for row in self.osdk_application_repository.resources_for_application(
                transaction=conn, tenant_id=ctx.tenant_id, app_id=app_id
            )
            for scope in row["scopes"]
        }
        if requested and not set(requested).issubset(granted):
            raise ValidationFailed("OSDK client allowed scopes must be a subset of application resources")
        return requested
