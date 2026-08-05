"""Configured trusted recipient directory for Action notification policies."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports.action_notification_recipient_directory import (
    ActionNotificationPolicy,
    ActionNotificationRecipient,
    NotificationDeliveryMode,
)


class ConfiguredActionNotificationRecipientDirectory:
    """Resolve policies from operator-owned configuration, never Action parameters."""

    profile_name = "configured-action-notification-directory"

    def __init__(self, policies_by_tenant: Mapping[str, Mapping[str, ActionNotificationPolicy]]) -> None:
        self._policies = {tenant: dict(policies) for tenant, policies in policies_by_tenant.items()}

    def policy_for(self, *, tenant_id: str, target_ref: str) -> ActionNotificationPolicy | None:
        return self._policies.get(tenant_id, {}).get(target_ref)


def action_notification_directory_from_json(raw: str) -> ConfiguredActionNotificationRecipientDirectory:
    """Parse bounded operator configuration into immutable policies."""
    loaded = json.loads(raw)
    if not isinstance(loaded, Mapping):
        raise ValueError("Action notification policy configuration must be an object")
    policies: dict[str, dict[str, ActionNotificationPolicy]] = {}
    for tenant_id, tenant_value in cast(Mapping[object, object], loaded).items():
        if not isinstance(tenant_id, str) or not isinstance(tenant_value, Mapping):
            raise ValueError("Action notification policy tenant entries must be objects")
        policies[tenant_id] = {
            str(target_ref): _policy(str(target_ref), value)
            for target_ref, value in cast(Mapping[object, object], tenant_value).items()
        }
    return ConfiguredActionNotificationRecipientDirectory(policies)


def _policy(target_ref: str, raw: object) -> ActionNotificationPolicy:
    if not target_ref.startswith("notification-policy:") or not isinstance(raw, Mapping):
        raise ValueError("Action notification policy target must use notification-policy:<name>")
    value = cast(Mapping[str, object], raw)
    mode = value.get("deliveryMode", "strict")
    if mode not in {"strict", "best_effort"}:
        raise ValueError("Action notification deliveryMode must be strict or best_effort")
    recipients_raw = value.get("recipients")
    if not isinstance(recipients_raw, Sequence) or isinstance(recipients_raw, str | bytes):
        raise ValueError("Action notification policy recipients must be a list")
    recipients = tuple(_recipient(item) for item in cast(Sequence[object], recipients_raw))
    if not recipients:
        raise ValueError("Action notification policy requires at least one recipient")
    if len({recipient.user_id for recipient in recipients}) != len(recipients):
        raise ValueError("Action notification policy recipients must be unique")
    return ActionNotificationPolicy(target_ref, cast(NotificationDeliveryMode, mode), recipients)


def _recipient(raw: object) -> ActionNotificationRecipient:
    if not isinstance(raw, Mapping):
        raise ValueError("Action notification recipient must be an object")
    value = cast(Mapping[str, object], raw)
    user_id = value.get("userId")
    roles = value.get("roles")
    if not isinstance(user_id, str) or not user_id.strip():
        raise ValueError("Action notification recipient userId is required")
    if not isinstance(roles, Sequence) or isinstance(roles, str | bytes):
        raise ValueError("Action notification recipient roles must be a list")
    normalized = tuple(sorted({str(role).strip() for role in roles if isinstance(role, str) and role.strip()}))
    if len(normalized) != len(roles):
        raise ValueError("Action notification recipient roles must be non-empty strings")
    return ActionNotificationRecipient(user_id.strip(), normalized)
