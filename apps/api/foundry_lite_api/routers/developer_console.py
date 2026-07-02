"""Developer Console OSDK application and SDK release routes."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Header, Query, Request
from foundry_lite.domain.errors import FoundryLiteError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import (
    JsonObject,
    OsdkApplicationClientRequest,
    OsdkApplicationCreateRequest,
    OsdkApplicationResourceRequest,
    OsdkApplicationResourcesUpdateRequest,
    OsdkArtifactDownloadTokenRequest,
    OsdkSdkCompatibilityWindowCreateRequest,
    OsdkSdkVersionCreateRequest,
)

router = APIRouter()


def _resource_payloads(resources: list[OsdkApplicationResourceRequest]) -> list[JsonObject]:
    return [cast(JsonObject, resource.model_dump(by_alias=True)) for resource in resources]


@router.post("/api/developer-console/osdk-applications")
def create_osdk_application(
    request: Request,
    payload: OsdkApplicationCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            runtime.foundry.developer_console.create_osdk_application(
                app_api_name=payload.app_api_name,
                display_name=payload.display_name,
                client_id=payload.client_id,
                resources=_resource_payloads(payload.resources),
                idempotency_key=idempotency_key,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/developer-console/osdk-applications")
def list_osdk_applications(request: Request) -> list[JsonObject]:
    try:
        return [
            cast(JsonObject, item)
            for item in runtime.foundry.developer_console.list_osdk_applications(ctx=_ctx(request))
        ]
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/developer-console/osdk-applications/{app_id}")
def get_osdk_application(request: Request, app_id: str) -> JsonObject:
    try:
        return cast(JsonObject, runtime.foundry.developer_console.get_osdk_application(app_id, ctx=_ctx(request)))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.put("/api/developer-console/osdk-applications/{app_id}/resources")
def update_osdk_application_resources(
    request: Request,
    app_id: str,
    payload: OsdkApplicationResourcesUpdateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            runtime.foundry.developer_console.update_osdk_application_resources(
                app_id,
                resources=_resource_payloads(payload.resources),
                idempotency_key=idempotency_key,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/developer-console/osdk-applications/{app_id}/clients")
def create_osdk_application_client(
    request: Request,
    app_id: str,
    payload: OsdkApplicationClientRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            runtime.foundry.developer_console.create_osdk_application_client(
                app_id,
                client_id=payload.client_id,
                redirect_uris=payload.redirect_uris,
                allowed_scopes=payload.allowed_scopes,
                access_token_ttl_seconds=payload.access_token_ttl_seconds,
                refresh_token_ttl_seconds=payload.refresh_token_ttl_seconds,
                idempotency_key=idempotency_key,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/developer-console/osdk-applications/{app_id}/clients")
def list_osdk_application_clients(request: Request, app_id: str) -> list[JsonObject]:
    try:
        return [
            cast(JsonObject, item)
            for item in runtime.foundry.developer_console.list_osdk_application_clients(app_id, ctx=_ctx(request))
        ]
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.put("/api/developer-console/osdk-applications/{app_id}/clients/{client_row_id}")
def update_osdk_application_client(
    request: Request,
    app_id: str,
    client_row_id: str,
    payload: OsdkApplicationClientRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            runtime.foundry.developer_console.update_osdk_application_client(
                app_id,
                client_row_id,
                status=payload.status,
                redirect_uris=payload.redirect_uris,
                allowed_scopes=payload.allowed_scopes,
                access_token_ttl_seconds=payload.access_token_ttl_seconds,
                refresh_token_ttl_seconds=payload.refresh_token_ttl_seconds,
                idempotency_key=idempotency_key,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/developer-console/osdk-applications/{app_id}/clients/{client_row_id}/deactivate")
def deactivate_osdk_application_client(
    request: Request,
    app_id: str,
    client_row_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            runtime.foundry.developer_console.deactivate_osdk_application_client(
                app_id, client_row_id, idempotency_key=idempotency_key, ctx=_ctx(request)
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/developer-console/osdk-applications/{app_id}/sdk-versions")
def create_osdk_sdk_version(
    request: Request,
    app_id: str,
    payload: OsdkSdkVersionCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            runtime.foundry.developer_console.create_osdk_sdk_version(
                app_id,
                language=payload.language,
                package_name=payload.package_name,
                requested_bump=payload.requested_bump,
                idempotency_key=idempotency_key,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/developer-console/osdk-applications/{app_id}/sdk-versions")
def list_osdk_sdk_versions(request: Request, app_id: str) -> list[JsonObject]:
    try:
        return [
            cast(JsonObject, item)
            for item in runtime.foundry.developer_console.list_osdk_sdk_versions(app_id, ctx=_ctx(request))
        ]
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/developer-console/osdk-applications/{app_id}/sdk-versions/{version_id}")
def get_osdk_sdk_version(request: Request, app_id: str, version_id: str) -> JsonObject:
    try:
        return cast(
            JsonObject, runtime.foundry.developer_console.get_osdk_sdk_version(app_id, version_id, ctx=_ctx(request))
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/developer-console/osdk-applications/{app_id}/sdk-versions/{version_id}/artifacts/{artifact_kind}")
def get_osdk_release_artifact(request: Request, app_id: str, version_id: str, artifact_kind: str) -> JsonObject:
    try:
        return cast(
            JsonObject,
            runtime.foundry.developer_console.osdk_release_artifact(
                app_id, version_id, artifact_kind, ctx=_ctx(request)
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/developer-console/osdk-applications/{app_id}/sdk-versions/{version_id}/channels/{channel}")
def promote_osdk_sdk_version(
    request: Request,
    app_id: str,
    version_id: str,
    channel: str,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            runtime.foundry.developer_console.promote_osdk_sdk_version(
                app_id, version_id, channel=channel, idempotency_key=idempotency_key, ctx=_ctx(request)
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/developer-console/osdk-applications/{app_id}/sdk-release-channels")
def list_osdk_release_channels(
    request: Request, app_id: str, language: str | None = Query(default=None)
) -> list[JsonObject]:
    try:
        return [
            cast(JsonObject, item)
            for item in runtime.foundry.developer_console.list_osdk_release_channels(
                app_id, language=language, ctx=_ctx(request)
            )
        ]
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/developer-console/osdk-applications/{app_id}/sdk-compatibility-windows")
def create_osdk_compatibility_window(
    request: Request,
    app_id: str,
    payload: OsdkSdkCompatibilityWindowCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            runtime.foundry.developer_console.create_osdk_compatibility_window(
                app_id,
                from_version_id=payload.from_version_id,
                to_version_id=payload.to_version_id,
                supported_until=payload.supported_until,
                idempotency_key=idempotency_key,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/developer-console/osdk-applications/{app_id}/sdk-compatibility-windows")
def list_osdk_compatibility_windows(request: Request, app_id: str) -> list[JsonObject]:
    try:
        return [
            cast(JsonObject, item)
            for item in runtime.foundry.developer_console.list_osdk_compatibility_windows(app_id, ctx=_ctx(request))
        ]
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/developer-console/osdk-applications/{app_id}/sdk-install-metadata")
def get_osdk_install_metadata(request: Request, app_id: str) -> JsonObject:
    try:
        return cast(JsonObject, runtime.foundry.developer_console.osdk_install_metadata(app_id, ctx=_ctx(request)))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post(
    "/api/developer-console/osdk-applications/{app_id}/sdk-versions/{version_id}/artifacts/{artifact_kind}/download-token"
)
def create_osdk_artifact_download_token(
    request: Request,
    app_id: str,
    version_id: str,
    artifact_kind: str,
    payload: OsdkArtifactDownloadTokenRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return cast(
            JsonObject,
            runtime.foundry.developer_console.create_osdk_artifact_download_token(
                app_id,
                version_id,
                artifact_kind,
                ttl_seconds=payload.ttl_seconds,
                idempotency_key=idempotency_key,
                ctx=_ctx(request),
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/developer-console/osdk-release-artifacts/download/{download_token}")
def get_osdk_release_artifact_by_download_token(request: Request, download_token: str) -> JsonObject:
    try:
        return cast(
            JsonObject,
            runtime.foundry.developer_console.osdk_release_artifact_by_download_token(
                download_token, ctx=_ctx(request)
            ),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc
