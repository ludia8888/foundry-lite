"""Webhook service-principal signature context construction."""

from __future__ import annotations

from foundry_lite.application.services.dataset.webhook_ingest import WebhookSignatureContext
from foundry_lite.domain.context import RequestContext


def webhook_signature_context(
    ctx: RequestContext,
    *,
    dataset_ref: str,
    connector_name: str,
    resource_name: str,
    require_service_principal_signature: bool,
) -> WebhookSignatureContext | None:
    if not require_service_principal_signature:
        return None
    return WebhookSignatureContext(
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.actor_user_id,
        dataset_ref=dataset_ref,
        connector_name=connector_name,
        resource_name=resource_name,
    )
