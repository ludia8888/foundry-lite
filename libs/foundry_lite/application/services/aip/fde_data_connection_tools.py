"""Official-name Data Connection MCP tools backed by governed Source services."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from foundry_lite.application.services.aip.fde_tool_result import (
    FdePlatformToolError,
    FdePlatformToolRequest,
    required_text,
    scope_value,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext


class FdeConnectorAuthor(Protocol):
    def create_connection(
        self,
        *,
        connector_name: str,
        display_name: str,
        base_url: str,
        auth: Mapping[str, object],
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...

    def upsert_resource(
        self,
        connector_name: str,
        resource_name: str,
        *,
        dataset_ref: str,
        resource_path: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        primary_key: Sequence[str] = (),
    ) -> dict[str, object]: ...


class FdeSourceAuthor(Protocol):
    def get_source(self, source_name: str, *, ctx: RequestContext | None = None) -> dict[str, object]: ...

    def create_webhook_listener(
        self,
        *,
        source_name: str,
        display_name: str,
        dataset_ref: str,
        connector_name: str,
        resource_name: str,
        signing_secret_ref: str,
        inbound_url: str,
        idempotency_key: str,
        ctx: RequestContext | None = None,
    ) -> dict[str, object]: ...


class FdeSourceManager(Protocol):
    def list_network_policies(self, *, ctx: RequestContext | None = None) -> list[dict[str, object]]: ...

    def create_network_policy(
        self,
        *,
        policy_name: str,
        display_name: str,
        mode: str,
        allowed_hosts: Sequence[str],
        idempotency_key: str,
        ctx: RequestContext | None = None,
        agent_id: str | None = None,
    ) -> dict[str, object]: ...


class FdeDataConnectionToolService(CoreService):
    """Create or inspect Sources without bypassing their native mutation paths."""

    required_dependencies = ()
    required_collaborators = (
        "connector_onboarding_service",
        "source_management_service",
        "source_onboarding_service",
    )
    connector_onboarding_service: FdeConnectorAuthor
    source_management_service: FdeSourceManager
    source_onboarding_service: FdeSourceAuthor

    def execute(self, ctx: RequestContext, request: FdePlatformToolRequest) -> dict[str, object]:
        tool_id = request.spec.tool_id
        if tool_id == "create_foundry_rest_api_data_source":
            return self._create_rest_source(ctx, request)
        if tool_id == "create_foundry_rest_api_data_source_webhook":
            return self._create_webhook(ctx, request)
        if tool_id == "view_foundry_rest_api_data_source_webhook":
            source_name = required_text(request.arguments, "sourceName")
            _require_source_scope(request.scope_ref, source_name)
            return self.source_onboarding_service.get_source(source_name, ctx=ctx)
        if tool_id == "get_or_create_network_egress_policy":
            return self._network_policy(ctx, request)
        raise FdePlatformToolError("unknown_fde_tool", f"unsupported Data Connection tool {tool_id}")

    def _create_rest_source(self, ctx: RequestContext, request: FdePlatformToolRequest) -> dict[str, object]:
        source_name = required_text(request.arguments, "sourceName")
        _require_source_scope(request.scope_ref, source_name)
        idempotency_key = required_text(request.arguments, "idempotencyKey")
        connection = self.connector_onboarding_service.create_connection(
            connector_name=source_name,
            display_name=required_text(request.arguments, "displayName"),
            base_url=required_text(request.arguments, "baseUrl"),
            auth=_mapping(request.arguments, "auth"),
            idempotency_key=idempotency_key,
            ctx=ctx,
        )
        resource = self.connector_onboarding_service.upsert_resource(
            source_name,
            required_text(request.arguments, "resourceName"),
            dataset_ref=required_text(request.arguments, "datasetRef"),
            resource_path=required_text(request.arguments, "resourcePath"),
            primary_key=_string_items(request.arguments.get("primaryKey")),
            idempotency_key=f"{idempotency_key}:resource",
            ctx=ctx,
        )
        return {"connection": connection, "resource": resource, "isGovernedSource": True}

    def _create_webhook(self, ctx: RequestContext, request: FdePlatformToolRequest) -> dict[str, object]:
        source_name = required_text(request.arguments, "sourceName")
        _require_source_scope(request.scope_ref, source_name)
        return self.source_onboarding_service.create_webhook_listener(
            source_name=source_name,
            display_name=required_text(request.arguments, "displayName"),
            dataset_ref=required_text(request.arguments, "datasetRef"),
            connector_name=required_text(request.arguments, "connectorName"),
            resource_name=required_text(request.arguments, "resourceName"),
            signing_secret_ref=required_text(request.arguments, "signingSecretRef"),
            inbound_url=required_text(request.arguments, "inboundUrl"),
            idempotency_key=required_text(request.arguments, "idempotencyKey"),
            ctx=ctx,
        )

    def _network_policy(self, ctx: RequestContext, request: FdePlatformToolRequest) -> dict[str, object]:
        policy_name = required_text(request.arguments, "policyName")
        existing = next(
            (
                item
                for item in self.source_management_service.list_network_policies(ctx=ctx)
                if item.get("policyName") == policy_name
            ),
            None,
        )
        if existing is not None:
            return {**existing, "isReplayed": True}
        return self.source_management_service.create_network_policy(
            policy_name=policy_name,
            display_name=required_text(request.arguments, "displayName"),
            mode=required_text(request.arguments, "mode"),
            allowed_hosts=_string_items(request.arguments.get("allowedHosts")),
            agent_id=_optional_text(request.arguments, "agentId"),
            idempotency_key=required_text(request.arguments, "idempotencyKey"),
            ctx=ctx,
        )


def _require_source_scope(scope_ref: str, source_name: str) -> None:
    if scope_ref.startswith("source:") and scope_value(scope_ref, "source:") != source_name:
        raise FdePlatformToolError("scope_mismatch", "Data Connection tool must match the selected Source scope")


def _mapping(value: Mapping[str, object], key: str) -> dict[str, object]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise FdePlatformToolError("schema_invalid", f"{key} must be an object")
    return {str(name): field for name, field in item.items()}


def _string_items(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise FdePlatformToolError("schema_invalid", "expected a list of strings")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise FdePlatformToolError("schema_invalid", "expected non-empty strings")
    return tuple(item.strip() for item in value if isinstance(item, str))


def _optional_text(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    return item.strip() if isinstance(item, str) and item.strip() else None
