from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import ProductWorkflowRun
from foundry_lite.application.services.connector_onboarding_service import ConnectorOnboardingService
from foundry_lite.domain.context import RequestContext
from foundry_lite.observability.tracing import trace_public_methods


@trace_public_methods
class ConnectorWorkspace:
    """Connector onboarding bounded context for frontend and operator flows."""

    def __init__(self, onboarding: ConnectorOnboardingService) -> None:
        self._onboarding = onboarding

    def create_connection(
        self,
        *,
        connector_name: str,
        display_name: str,
        base_url: str,
        auth: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
        rate_limit_per_minute: int | None = None,
        allow_private_network: bool = False,
    ) -> dict[str, object]:
        return self._onboarding.create_connection(
            connector_name=connector_name,
            display_name=display_name,
            base_url=base_url,
            auth=auth,
            idempotency_key=idempotency_key,
            ctx=ctx,
            rate_limit_per_minute=rate_limit_per_minute,
            allow_private_network=allow_private_network,
        )

    def list_connections(self, *, ctx: RequestContext | None = None) -> list[dict[str, object]]:
        return self._onboarding.list_connections(ctx=ctx)

    def get_connection(self, connector_name: str, *, ctx: RequestContext | None = None) -> dict[str, object]:
        return self._onboarding.get_connection(connector_name, ctx=ctx)

    def update_connection(
        self,
        connector_name: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        display_name: str | None = None,
        base_url: str | None = None,
        auth: Mapping[str, object] | None = None,
        rate_limit_per_minute: int | None = None,
        allow_private_network: bool | None = None,
        status: str | None = None,
    ) -> dict[str, object]:
        return self._onboarding.update_connection(
            connector_name,
            idempotency_key=idempotency_key,
            ctx=ctx,
            display_name=display_name,
            base_url=base_url,
            auth=auth,
            rate_limit_per_minute=rate_limit_per_minute,
            allow_private_network=allow_private_network,
            status=status,
        )

    def upsert_resource(
        self,
        connector_name: str,
        resource_name: str,
        *,
        dataset_ref: str,
        resource_path: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        pagination: Mapping[str, object] | None = None,
        schema_columns: Sequence[str] = (),
        primary_key: Sequence[str] = (),
    ) -> dict[str, object]:
        return self._onboarding.upsert_resource(
            connector_name,
            resource_name,
            dataset_ref=dataset_ref,
            resource_path=resource_path,
            idempotency_key=idempotency_key,
            ctx=ctx,
            pagination=pagination,
            schema_columns=schema_columns,
            primary_key=primary_key,
        )

    def test_resource(
        self, connector_name: str, resource_name: str, *, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self._onboarding.test_resource(connector_name, resource_name, ctx=ctx)

    def start_resource_sync(
        self,
        connector_name: str,
        resource_name: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        sync_name: str | None = None,
        transaction_type: str = "SNAPSHOT",
    ) -> ProductWorkflowRun:
        return self._onboarding.start_resource_sync(
            connector_name,
            resource_name,
            idempotency_key=idempotency_key,
            ctx=ctx,
            sync_name=sync_name,
            transaction_type=transaction_type,
        )

    def run_registered_sync_activity(
        self, payload: Mapping[str, object], *, ctx: RequestContext | None = None
    ) -> dict[str, object]:
        return self._onboarding.run_registered_sync_activity(payload, ctx=ctx)
