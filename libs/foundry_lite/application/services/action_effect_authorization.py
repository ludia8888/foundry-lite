"""Permission and registered-target checks for canonical Action effects."""

from __future__ import annotations

from typing import Protocol

from foundry_lite.application.ports import OsdkResourceOperation, OsdkResourceType
from foundry_lite.application.ports.action_notification_recipient_directory import (
    ActionNotificationRecipientDirectory,
)
from foundry_lite.application.ports.connector_registry_repository import ConnectorRegistryRepository
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.domain.action_runtime.action_contract import ActionDefinitionV3
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.security.policy import PolicyService


class EffectScopeBoundary(Protocol):
    """Application restriction boundary needed by effect authorization."""

    def require_resource_scope(
        self,
        ctx: RequestContext,
        *,
        resource_type: OsdkResourceType,
        resource_api_name: str,
        operation: OsdkResourceOperation,
    ) -> None: ...


def authorize_action_effects(
    transaction: TransactionContext,
    ctx: RequestContext,
    policy: PolicyService,
    scope: EffectScopeBoundary,
    repository: ConnectorRegistryRepository,
    notification_directory: ActionNotificationRecipientDirectory,
    contract: ActionDefinitionV3,
) -> None:
    """Require actor, application, connector, and target permissions for every effect."""
    if not contract.effects:
        return
    if contract.source_version < 3:
        return
    policy.require(ctx, "action:effect:execute")
    for effect in contract.effects:
        if effect.kind in {"webhook", "connector_command"}:
            _require_connector_target(transaction, ctx, scope, repository, effect.target_ref)
        else:
            _require_governed_stream_target(effect.kind, effect.target_ref, ctx.tenant_id, notification_directory)


def _require_connector_target(
    transaction: TransactionContext,
    ctx: RequestContext,
    scope: EffectScopeBoundary,
    repository: ConnectorRegistryRepository,
    target_ref: str,
) -> None:
    connector_name, resource_name = _connector_parts(target_ref)
    connection = repository.connection_by_name(
        transaction=transaction, tenant_id=ctx.tenant_id, connector_name=connector_name
    )
    resource = repository.resource_by_name(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        connector_name=connector_name,
        resource_name=resource_name,
    )
    if connection is None or connection["status"] != "active" or resource is None:
        raise ValidationFailed(
            "Action effect target must resolve to an active registered connector resource",
            details={"targetRef": target_ref},
        )
    scope.require_resource_scope(ctx, resource_type="connector", resource_api_name=connector_name, operation="execute")


def _connector_parts(target_ref: str) -> tuple[str, str]:
    parts = target_ref.removeprefix("connector:").split("/", 1) if target_ref.startswith("connector:") else []
    if len(parts) != 2 or not all(part.strip() for part in parts):
        raise ValidationFailed(
            "Action webhook targetRef must use connector:<name>/<resource>",
            details={"targetRef": target_ref},
        )
    return parts[0], parts[1]


def _require_governed_stream_target(
    kind: str,
    target_ref: str,
    tenant_id: str,
    notification_directory: ActionNotificationRecipientDirectory,
) -> None:
    prefixes = {"event": "topic:", "notification": "notification-policy:", "schedule_build": "schedule:"}
    expected = prefixes.get(kind)
    if expected is None or not target_ref.startswith(expected):
        raise ValidationFailed(
            "Action effect targetRef is not valid for its effect kind",
            details={"kind": kind, "targetRef": target_ref},
        )
    if kind == "notification" and notification_directory.policy_for(tenant_id=tenant_id, target_ref=target_ref) is None:
        raise ValidationFailed(
            "Action notification targetRef is not a registered recipient policy",
            details={"targetRef": target_ref},
        )
