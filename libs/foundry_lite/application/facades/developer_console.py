"""Thin facade entrypoints for developer console workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import (
    OsdkApplicationBundle,
    OsdkApplicationClientRow,
    OsdkMcpServerRow,
    OsdkSdkCompatibilityWindowRow,
    OsdkSdkReleaseChannelRow,
    OsdkSdkVersionBundle,
    OsdkSdkVersionRow,
    RuntimeJsonObject,
)
from foundry_lite.application.services.osdk_application_service import OsdkApplicationService
from foundry_lite.domain.context import RequestContext
from foundry_lite.observability.tracing import trace_public_methods


@trace_public_methods
class DeveloperConsole:
    """Developer Console-lite facade for OSDK app scopes and local SDK releases."""

    def __init__(self, osdk_applications: OsdkApplicationService) -> None:
        self._osdk_applications = osdk_applications

    def create_osdk_application(
        self,
        *,
        app_api_name: str,
        display_name: str,
        client_id: str | None = None,
        resources: Sequence[Mapping[str, object]] = (),
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> OsdkApplicationBundle:
        return self._osdk_applications.create_application(
            ctx=ctx,
            app_api_name=app_api_name,
            display_name=display_name,
            client_id=client_id,
            resources=resources,
            idempotency_key=idempotency_key,
        )

    def list_osdk_applications(self, *, ctx: RequestContext | None = None) -> list[OsdkApplicationBundle]:
        return self._osdk_applications.list_applications(ctx=ctx)

    def get_osdk_application(self, app_id: str, *, ctx: RequestContext | None = None) -> OsdkApplicationBundle:
        return self._osdk_applications.get_application(app_id, ctx=ctx)

    def update_osdk_application_resources(
        self,
        app_id: str,
        *,
        resources: Sequence[Mapping[str, object]],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> OsdkApplicationBundle:
        return self._osdk_applications.update_resources(
            app_id,
            ctx=ctx,
            resources=resources,
            idempotency_key=idempotency_key,
        )

    def configure_ontology_mcp_server(
        self,
        app_id: str,
        *,
        status: str,
        description_markdown: str,
        allowed_origins: Sequence[str] = (),
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> OsdkMcpServerRow:
        return self._osdk_applications.configure_mcp_server(
            app_id,
            status=status,
            description_markdown=description_markdown,
            allowed_origins=allowed_origins,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def get_ontology_mcp_server(self, app_id: str, *, ctx: RequestContext | None = None) -> OsdkMcpServerRow:
        return self._osdk_applications.get_mcp_server(app_id, ctx=ctx)

    def list_ontology_mcp_hub(self, *, ctx: RequestContext | None = None) -> list[RuntimeJsonObject]:
        return self._osdk_applications.list_mcp_hub(ctx=ctx)

    def create_osdk_application_client(
        self,
        app_id: str,
        *,
        client_id: str,
        redirect_uris: Sequence[str],
        allowed_scopes: Sequence[str],
        access_token_ttl_seconds: int = 900,
        refresh_token_ttl_seconds: int = 2_592_000,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> OsdkApplicationClientRow:
        return self._osdk_applications.create_client(
            app_id,
            ctx=ctx,
            client_id=client_id,
            redirect_uris=redirect_uris,
            allowed_scopes=allowed_scopes,
            access_token_ttl_seconds=access_token_ttl_seconds,
            refresh_token_ttl_seconds=refresh_token_ttl_seconds,
            idempotency_key=idempotency_key,
        )

    def list_osdk_application_clients(
        self, app_id: str, *, ctx: RequestContext | None = None
    ) -> list[OsdkApplicationClientRow]:
        return self._osdk_applications.list_clients(app_id, ctx=ctx)

    def rotate_osdk_application_client_secret(
        self,
        app_id: str,
        client_row_id: str,
        *,
        reason: str | None = None,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> RuntimeJsonObject:
        return self._osdk_applications.rotate_client_secret(
            app_id,
            client_row_id,
            reason=reason,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def revoke_osdk_application_client_secret(
        self,
        app_id: str,
        client_row_id: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> RuntimeJsonObject:
        return self._osdk_applications.revoke_client_secret(
            app_id,
            client_row_id,
            idempotency_key=idempotency_key,
            ctx=ctx,
        )

    def list_osdk_application_client_secret_versions(
        self, app_id: str, client_row_id: str, *, ctx: RequestContext | None = None
    ) -> list[RuntimeJsonObject]:
        return self._osdk_applications.list_client_secret_versions(app_id, client_row_id, ctx=ctx)

    def update_osdk_application_client(
        self,
        app_id: str,
        client_row_id: str,
        *,
        status: str,
        redirect_uris: Sequence[str],
        allowed_scopes: Sequence[str],
        access_token_ttl_seconds: int = 900,
        refresh_token_ttl_seconds: int = 2_592_000,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> OsdkApplicationClientRow:
        return self._osdk_applications.update_client(
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

    def deactivate_osdk_application_client(
        self, app_id: str, client_row_id: str, *, idempotency_key: str, ctx: RequestContext | None = None
    ) -> OsdkApplicationClientRow:
        return self._osdk_applications.deactivate_client(
            app_id, client_row_id, ctx=ctx, idempotency_key=idempotency_key
        )

    def create_osdk_sdk_version(
        self,
        app_id: str,
        *,
        language: str,
        package_name: str | None = None,
        requested_bump: str | None = None,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> OsdkSdkVersionBundle:
        return self._osdk_applications.create_sdk_version(
            app_id,
            ctx=ctx,
            language=language,
            package_name=package_name,
            requested_bump=requested_bump,
            idempotency_key=idempotency_key,
        )

    def list_osdk_sdk_versions(self, app_id: str, *, ctx: RequestContext | None = None) -> list[OsdkSdkVersionRow]:
        return self._osdk_applications.list_sdk_versions(app_id, ctx=ctx)

    def get_osdk_sdk_version(
        self, app_id: str, version_id: str, *, ctx: RequestContext | None = None
    ) -> OsdkSdkVersionBundle:
        return self._osdk_applications.get_sdk_version(app_id, version_id, ctx=ctx)

    def osdk_release_artifact(
        self, app_id: str, version_id: str, artifact_kind: str, *, ctx: RequestContext | None = None
    ) -> RuntimeJsonObject:
        return self._osdk_applications.read_release_artifact(app_id, version_id, artifact_kind, ctx=ctx)

    def promote_osdk_sdk_version(
        self,
        app_id: str,
        version_id: str,
        *,
        channel: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> OsdkSdkReleaseChannelRow:
        return self._osdk_applications.promote_sdk_version(
            app_id, version_id, ctx=ctx, channel=channel, idempotency_key=idempotency_key
        )

    def list_osdk_release_channels(
        self, app_id: str, *, language: str | None = None, ctx: RequestContext | None = None
    ) -> list[OsdkSdkReleaseChannelRow]:
        return self._osdk_applications.list_release_channels(app_id, ctx=ctx, language=language)

    def create_osdk_compatibility_window(
        self,
        app_id: str,
        *,
        from_version_id: str,
        to_version_id: str,
        supported_until: str | None = None,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> OsdkSdkCompatibilityWindowRow:
        return self._osdk_applications.create_compatibility_window(
            app_id,
            ctx=ctx,
            from_version_id=from_version_id,
            to_version_id=to_version_id,
            supported_until=supported_until,
            idempotency_key=idempotency_key,
        )

    def list_osdk_compatibility_windows(
        self, app_id: str, *, ctx: RequestContext | None = None
    ) -> list[OsdkSdkCompatibilityWindowRow]:
        return self._osdk_applications.list_compatibility_windows(app_id, ctx=ctx)

    def osdk_install_metadata(self, app_id: str, *, ctx: RequestContext | None = None) -> RuntimeJsonObject:
        return self._osdk_applications.install_metadata(app_id, ctx=ctx)

    def create_osdk_artifact_download_token(
        self,
        app_id: str,
        version_id: str,
        artifact_kind: str,
        *,
        ttl_seconds: int = 900,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> RuntimeJsonObject:
        return self._osdk_applications.create_artifact_download_token(
            app_id,
            version_id,
            artifact_kind,
            ctx=ctx,
            ttl_seconds=ttl_seconds,
            idempotency_key=idempotency_key,
        )

    def osdk_release_artifact_by_download_token(
        self, token: str, *, ctx: RequestContext | None = None
    ) -> RuntimeJsonObject:
        return self._osdk_applications.read_release_artifact_by_download_token(token, ctx=ctx)
