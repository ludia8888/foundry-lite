"""Webhook ingest security helpers: body limits, signing keys, service identity."""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from foundry_lite.application.upload_limits import max_webhook_body_bytes
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, ValidationFailed
from pydantic import ValidationError

from foundry_lite_api import runtime
from foundry_lite_api.request_context import _ctx, _request_id
from foundry_lite_api.schemas import JsonObject, WebhookPayloadRequest

WEBHOOK_SIGNING_KEY_ENV = "FOUNDRY_LITE_WEBHOOK_SIGNING_KEY"


WEBHOOK_SIGNING_KEY_NAME = "webhook_signing_key"


WEBHOOK_SERVICE_PRINCIPAL_HEADER = "X-Foundry-Lite-Service-Principal"


WEBHOOK_SERVICE_TENANT_HEADER = "X-Foundry-Lite-Tenant-ID"


WEBHOOK_SERVICE_ROLE = "connector_ingest"


WEBHOOK_SERVICE_ACTOR_PREFIX = "service-principal:"


# Label domain-separates the webhook-signing KDF from any other use of the root
# secret, and carries a version so the derivation can be rotated later.
_WEBHOOK_TENANT_KEY_DERIVATION_LABEL = b"foundry-lite/webhook-signing/v1"


@dataclass(frozen=True)
class WebhookRequestContext:
    ctx: RequestContext
    require_service_principal_signature: bool


async def _bounded_webhook_body(request: Request) -> bytes:
    max_bytes = max_webhook_body_bytes()
    content_length = _request_content_length(request)
    if content_length is not None and content_length > max_bytes:
        raise _webhook_body_too_large(content_length, max_bytes)
    chunks: list[bytes] = []
    size_bytes = 0
    async for chunk in request.stream():
        size_bytes += len(chunk)
        if size_bytes > max_bytes:
            raise _webhook_body_too_large(size_bytes, max_bytes)
        if chunk:
            chunks.append(chunk)
    return b"".join(chunks)


def _request_content_length(request: Request) -> int | None:
    value = request.headers.get("content-length")
    if value is None:
        return None
    try:
        content_length = int(value)
    except ValueError as exc:
        raise ValidationFailed("invalid content-length header", details={"header": "content-length"}) from exc
    if content_length < 0:
        raise ValidationFailed("invalid content-length header", details={"header": "content-length"})
    return content_length


def _webhook_body_too_large(size_bytes: int, max_bytes: int) -> ValidationFailed:
    return ValidationFailed(
        "webhook body exceeds configured size limit",
        details={"size_bytes": size_bytes, "max_bytes": max_bytes},
    )


def _webhook_payload_request(raw_body: bytes) -> WebhookPayloadRequest:
    try:
        return WebhookPayloadRequest.model_validate_json(raw_body)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc


def _webhook_payload(value: WebhookPayloadRequest) -> JsonObject:
    return {str(key): item for key, item in (value.model_extra or {}).items()}


def _webhook_request_context(
    request: Request,
    *,
    service_principal: str | None,
    service_tenant_id: str | None,
) -> WebhookRequestContext:
    principal_id = _webhook_identity_header(service_principal, WEBHOOK_SERVICE_PRINCIPAL_HEADER)
    tenant_id = _webhook_identity_header(service_tenant_id, WEBHOOK_SERVICE_TENANT_HEADER)
    if principal_id is None and tenant_id is None:
        return WebhookRequestContext(ctx=_ctx(request), require_service_principal_signature=False)
    if principal_id is None or tenant_id is None:
        raise ValidationFailed(
            "webhook service-principal auth requires tenant and principal headers",
            details={"required_headers": [WEBHOOK_SERVICE_TENANT_HEADER, WEBHOOK_SERVICE_PRINCIPAL_HEADER]},
        )
    ctx = RequestContext(
        tenant_id=tenant_id,
        actor_user_id=f"{WEBHOOK_SERVICE_ACTOR_PREFIX}{principal_id}",
        request_id=_request_id(request, f"api-{time.time_ns()}"),
        roles=(WEBHOOK_SERVICE_ROLE,),
    )
    return WebhookRequestContext(ctx=ctx, require_service_principal_signature=True)


def _webhook_identity_header(value: str | None, header_name: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized == "" or any(char.isspace() for char in normalized):
        raise ValidationFailed("invalid webhook service-principal header", details={"header": header_name})
    return normalized


def _webhook_signing_key(
    ctx: RequestContext,
    dataset_ref: str,
    connector_name: str,
    resource_name: str,
) -> str:
    """Return the per-tenant signing key for a connector webhook.

    The connector webhook endpoint derives the request tenant from a client-set
    header, so verifying the HMAC with one deployment-wide secret let any tenant
    that knew that shared secret sign a request as another tenant and inject rows
    into the victim's dataset. Instead, derive a distinct key per tenant from a
    single root secret: an operator hands each tenant only its own derived key, so
    a tenant cannot compute — and therefore cannot forge a signature valid under —
    another tenant's key without the root secret. An empty root is returned
    unchanged so the "webhook secret is not configured" check still fires.
    """
    try:
        root_secret = runtime.foundry.secret_provider.get_secret(WEBHOOK_SIGNING_KEY_NAME).value
    except FoundryLiteError as exc:
        _audit_webhook_secret_failure(ctx, dataset_ref, connector_name, resource_name, exc)
        raise
    if not root_secret:
        return root_secret
    return _derive_tenant_webhook_signing_key(root_secret, ctx.tenant_id)


def _derive_tenant_webhook_signing_key(root_secret: str, tenant_id: str) -> str:
    return hmac.new(
        root_secret.encode("utf-8"),
        _WEBHOOK_TENANT_KEY_DERIVATION_LABEL + b"\0" + tenant_id.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _audit_webhook_secret_failure(
    ctx: RequestContext,
    dataset_ref: str,
    connector_name: str,
    resource_name: str,
    exc: FoundryLiteError,
) -> None:
    runtime.foundry.operations.record_failure_audit(
        ctx=ctx,
        event_type="webhook.secret_resolution_failed",
        resource_type="webhook",
        resource_id=f"{connector_name}:{resource_name}",
        action="webhook:ingest",
        exc=exc,
        decision="deny",
        before_ref={"dataset_ref": dataset_ref},
        after_ref={"secret_name": WEBHOOK_SIGNING_KEY_NAME, "env_var": WEBHOOK_SIGNING_KEY_ENV},
        adapter="secret_provider.get_secret",
    )
