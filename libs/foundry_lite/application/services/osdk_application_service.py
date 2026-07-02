"""Compatibility entrypoint for Developer Console OSDK application workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import (
    OsdkApplicationBundle,
    OsdkApplicationClientRow,
    OsdkResourceOperation,
    OsdkResourceType,
    OsdkSdkCompatibilityWindowRow,
    OsdkSdkReleaseChannelRow,
    OsdkSdkVersionBundle,
    OsdkSdkVersionRow,
    RuntimeJsonObject,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.osdk_application_clients import OsdkApplicationClientService
from foundry_lite.application.services.osdk_application_records import scope_for as scope_for
from foundry_lite.application.services.osdk_application_scope import OsdkApplicationScopeService
from foundry_lite.application.services.osdk_application_sdk import OsdkApplicationSdkService
from foundry_lite.domain.context import RequestContext


class OsdkApplicationService(CoreService):
    """Stable public service surface backed by explicit OSDK use-case services."""

    required_dependencies = ()
    required_collaborators = (
        "osdk_application_client_service",
        "osdk_application_scope_service",
        "osdk_application_sdk_service",
    )
    osdk_application_client_service: OsdkApplicationClientService
    osdk_application_scope_service: OsdkApplicationScopeService
    osdk_application_sdk_service: OsdkApplicationSdkService

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
        return self.osdk_application_scope_service.create_application(
            ctx=ctx,
            app_api_name=app_api_name,
            display_name=display_name,
            client_id=client_id,
            resources=resources,
            idempotency_key=idempotency_key,
        )

    def list_applications(self, *, ctx: RequestContext | None = None) -> list[OsdkApplicationBundle]:
        return self.osdk_application_scope_service.list_applications(ctx=ctx)

    def get_application(self, app_id: str, *, ctx: RequestContext | None = None) -> OsdkApplicationBundle:
        return self.osdk_application_scope_service.get_application(app_id, ctx=ctx)

    def update_resources(
        self,
        app_id: str,
        *,
        ctx: RequestContext | None = None,
        resources: Sequence[Mapping[str, object]],
        idempotency_key: str,
    ) -> OsdkApplicationBundle:
        return self.osdk_application_scope_service.update_resources(
            app_id, ctx=ctx, resources=resources, idempotency_key=idempotency_key
        )

    def require_resource_scope(
        self,
        ctx: RequestContext,
        *,
        resource_type: OsdkResourceType,
        resource_api_name: str,
        operation: OsdkResourceOperation,
    ) -> None:
        self.osdk_application_scope_service.require_resource_scope(
            ctx,
            resource_type=resource_type,
            resource_api_name=resource_api_name,
            operation=operation,
        )

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
        return self.osdk_application_client_service.create_client(
            app_id,
            ctx=ctx,
            client_id=client_id,
            redirect_uris=redirect_uris,
            allowed_scopes=allowed_scopes,
            access_token_ttl_seconds=access_token_ttl_seconds,
            refresh_token_ttl_seconds=refresh_token_ttl_seconds,
            idempotency_key=idempotency_key,
        )

    def list_clients(self, app_id: str, *, ctx: RequestContext | None = None) -> list[OsdkApplicationClientRow]:
        return self.osdk_application_client_service.list_clients(app_id, ctx=ctx)

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
        return self.osdk_application_client_service.update_client(
            app_id,
            client_row_id,
            ctx=ctx,
            status=status,
            redirect_uris=redirect_uris,
            allowed_scopes=allowed_scopes,
            access_token_ttl_seconds=access_token_ttl_seconds,
            refresh_token_ttl_seconds=refresh_token_ttl_seconds,
            idempotency_key=idempotency_key,
        )

    def deactivate_client(
        self, app_id: str, client_row_id: str, *, ctx: RequestContext | None = None, idempotency_key: str
    ) -> OsdkApplicationClientRow:
        return self.osdk_application_client_service.deactivate_client(
            app_id, client_row_id, ctx=ctx, idempotency_key=idempotency_key
        )

    def create_sdk_version(
        self,
        app_id: str,
        *,
        ctx: RequestContext | None = None,
        language: str,
        package_name: str | None = None,
        requested_bump: str | None = None,
        idempotency_key: str,
    ) -> OsdkSdkVersionBundle:
        return self.osdk_application_sdk_service.create_sdk_version(
            app_id,
            ctx=ctx,
            language=language,
            package_name=package_name,
            requested_bump=requested_bump,
            idempotency_key=idempotency_key,
        )

    def list_sdk_versions(self, app_id: str, *, ctx: RequestContext | None = None) -> list[OsdkSdkVersionRow]:
        return self.osdk_application_sdk_service.list_sdk_versions(app_id, ctx=ctx)

    def get_sdk_version(
        self, app_id: str, version_id: str, *, ctx: RequestContext | None = None
    ) -> OsdkSdkVersionBundle:
        return self.osdk_application_sdk_service.get_sdk_version(app_id, version_id, ctx=ctx)

    def read_release_artifact(
        self, app_id: str, version_id: str, artifact_kind: str, *, ctx: RequestContext | None = None
    ) -> RuntimeJsonObject:
        return self.osdk_application_sdk_service.read_release_artifact(app_id, version_id, artifact_kind, ctx=ctx)

    def promote_sdk_version(
        self,
        app_id: str,
        version_id: str,
        *,
        ctx: RequestContext | None = None,
        channel: str,
        idempotency_key: str,
    ) -> OsdkSdkReleaseChannelRow:
        return self.osdk_application_sdk_service.promote_sdk_version(
            app_id, version_id, ctx=ctx, channel=channel, idempotency_key=idempotency_key
        )

    def list_release_channels(
        self, app_id: str, *, ctx: RequestContext | None = None, language: str | None = None
    ) -> list[OsdkSdkReleaseChannelRow]:
        return self.osdk_application_sdk_service.list_release_channels(app_id, ctx=ctx, language=language)

    def create_compatibility_window(
        self,
        app_id: str,
        *,
        ctx: RequestContext | None = None,
        from_version_id: str,
        to_version_id: str,
        supported_until: str | None = None,
        idempotency_key: str,
    ) -> OsdkSdkCompatibilityWindowRow:
        return self.osdk_application_sdk_service.create_compatibility_window(
            app_id,
            ctx=ctx,
            from_version_id=from_version_id,
            to_version_id=to_version_id,
            supported_until=supported_until,
            idempotency_key=idempotency_key,
        )

    def list_compatibility_windows(
        self, app_id: str, *, ctx: RequestContext | None = None
    ) -> list[OsdkSdkCompatibilityWindowRow]:
        return self.osdk_application_sdk_service.list_compatibility_windows(app_id, ctx=ctx)

    def install_metadata(self, app_id: str, *, ctx: RequestContext | None = None) -> RuntimeJsonObject:
        return self.osdk_application_sdk_service.install_metadata(app_id, ctx=ctx)

    def create_artifact_download_token(
        self,
        app_id: str,
        version_id: str,
        artifact_kind: str,
        *,
        ctx: RequestContext | None = None,
        ttl_seconds: int = 900,
        idempotency_key: str,
    ) -> RuntimeJsonObject:
        return self.osdk_application_sdk_service.create_artifact_download_token(
            app_id,
            version_id,
            artifact_kind,
            ctx=ctx,
            ttl_seconds=ttl_seconds,
            idempotency_key=idempotency_key,
        )

    def read_release_artifact_by_download_token(
        self, token: str, *, ctx: RequestContext | None = None
    ) -> RuntimeJsonObject:
        return self.osdk_application_sdk_service.read_release_artifact_by_download_token(token, ctx=ctx)
