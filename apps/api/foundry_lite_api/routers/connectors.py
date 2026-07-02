"""REST connector connection, resource, and webhook ingest routes."""

from __future__ import annotations

from typing import cast

from fastapi import APIRouter, Header, Query, Request
from foundry_lite.application.ports import ProductWorkflowRun
from foundry_lite.domain.errors import FoundryLiteError

from foundry_lite_api import runtime
from foundry_lite_api.errors import _handle_error
from foundry_lite_api.request_context import _ctx
from foundry_lite_api.schemas import (
    ConnectorResourceSyncStartRequest,
    JsonObject,
    RestConnectorAuthRequest,
    RestConnectorConnectionCreateRequest,
    RestConnectorConnectionUpdateRequest,
    RestConnectorPaginationRequest,
    RestConnectorResourceUpsertRequest,
)
from foundry_lite_api.webhooks import (
    WEBHOOK_SERVICE_PRINCIPAL_HEADER,
    WEBHOOK_SERVICE_TENANT_HEADER,
    _bounded_webhook_body,
    _webhook_payload,
    _webhook_payload_request,
    _webhook_request_context,
    _webhook_signing_key,
)

router = APIRouter()


@router.post("/api/connectors/connections")
def create_connector_connection(
    request: Request,
    payload: RestConnectorConnectionCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.connectors.create_connection(
            connector_name=payload.connector_name,
            display_name=payload.display_name,
            base_url=payload.base_url,
            auth=_connector_auth_payload(payload.auth),
            rate_limit_per_minute=payload.rate_limit_per_minute,
            allow_private_network=payload.allow_private_network,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/connectors/connections")
def list_connector_connections(request: Request) -> list[JsonObject]:
    try:
        return runtime.foundry.connectors.list_connections(ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.get("/api/connectors/connections/{connector_name}")
def get_connector_connection(request: Request, connector_name: str) -> JsonObject:
    try:
        return runtime.foundry.connectors.get_connection(connector_name, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.patch("/api/connectors/connections/{connector_name}")
def update_connector_connection(
    request: Request,
    connector_name: str,
    payload: RestConnectorConnectionUpdateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.connectors.update_connection(
            connector_name,
            display_name=payload.display_name,
            base_url=payload.base_url,
            auth=_connector_auth_payload(payload.auth) if payload.auth is not None else None,
            rate_limit_per_minute=payload.rate_limit_per_minute,
            allow_private_network=payload.allow_private_network,
            status=payload.status,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.put("/api/connectors/connections/{connector_name}/resources/{resource_name}")
def upsert_connector_resource(
    request: Request,
    connector_name: str,
    resource_name: str,
    payload: RestConnectorResourceUpsertRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> JsonObject:
    try:
        return runtime.foundry.connectors.upsert_resource(
            connector_name,
            resource_name,
            dataset_ref=payload.dataset_ref,
            resource_path=payload.resource_path,
            pagination=_connector_pagination_payload(payload.pagination),
            schema_columns=payload.schema_columns,
            primary_key=payload.primary_key,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/connectors/connections/{connector_name}/resources/{resource_name}/test")
def test_connector_resource(request: Request, connector_name: str, resource_name: str) -> JsonObject:
    try:
        return runtime.foundry.connectors.test_resource(connector_name, resource_name, ctx=_ctx(request))
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/connectors/connections/{connector_name}/resources/{resource_name}/sync/start")
def start_connector_resource_sync(
    request: Request,
    connector_name: str,
    resource_name: str,
    payload: ConnectorResourceSyncStartRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> ProductWorkflowRun:
    try:
        return runtime.foundry.connectors.start_resource_sync(
            connector_name,
            resource_name,
            sync_name=payload.sync_name,
            idempotency_key=idempotency_key,
            ctx=_ctx(request),
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


@router.post("/api/connectors/webhooks/{connector_name}/{resource_name}")
async def ingest_webhook(
    request: Request,
    connector_name: str,
    resource_name: str,
    dataset_ref: str = Query(alias="datasetRef"),
    signature: str = Header(alias="X-Foundry-Lite-Signature"),
    signature_timestamp: str = Header(alias="X-Foundry-Lite-Timestamp"),
    event_id: str | None = Header(default=None, alias="X-Foundry-Lite-Event-ID"),
    service_principal: str | None = Header(default=None, alias=WEBHOOK_SERVICE_PRINCIPAL_HEADER),
    service_tenant_id: str | None = Header(default=None, alias=WEBHOOK_SERVICE_TENANT_HEADER),
):
    try:
        raw_body = await _bounded_webhook_body(request)
        payload = _webhook_payload_request(raw_body)
        webhook_ctx = _webhook_request_context(
            request,
            service_principal=service_principal,
            service_tenant_id=service_tenant_id,
        )
        ctx = webhook_ctx.ctx
        return runtime.foundry.datasets.ingest_webhook_event(
            dataset_ref,
            connector_name=connector_name,
            resource_name=resource_name,
            payload=_webhook_payload(payload),
            raw_body=raw_body,
            signature=signature,
            signature_timestamp=signature_timestamp,
            secret=_webhook_signing_key(ctx, dataset_ref, connector_name, resource_name),
            event_id=event_id,
            require_service_principal_signature=webhook_ctx.require_service_principal_signature,
            ctx=ctx,
        )
    except FoundryLiteError as exc:
        raise _handle_error(exc, request) from exc


def _connector_auth_payload(value: RestConnectorAuthRequest) -> JsonObject:
    return cast(JsonObject, value.model_dump(by_alias=True, exclude_none=True))


def _connector_pagination_payload(value: RestConnectorPaginationRequest) -> JsonObject:
    return cast(JsonObject, value.model_dump(by_alias=True))
