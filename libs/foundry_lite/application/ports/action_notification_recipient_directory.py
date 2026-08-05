"""Trusted directory port for governed Action notification recipient policies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

NotificationDeliveryMode = Literal["strict", "best_effort"]


@dataclass(frozen=True, slots=True)
class ActionNotificationRecipient:
    """One current principal resolved from a trusted identity directory."""

    user_id: str
    roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ActionNotificationPolicy:
    """Registered target policy; Action definitions reference it by name only."""

    target_ref: str
    delivery_mode: NotificationDeliveryMode
    recipients: tuple[ActionNotificationRecipient, ...]


class ActionNotificationRecipientDirectory(Protocol):
    """Resolve current recipients for a tenant-scoped registered policy."""

    @property
    def profile_name(self) -> str: ...

    def policy_for(self, *, tenant_id: str, target_ref: str) -> ActionNotificationPolicy | None: ...


class UnavailableActionNotificationRecipientDirectory:
    """Fail-closed directory used when no trusted recipient source is configured."""

    profile_name = "unavailable-action-notification-directory"

    def policy_for(self, *, tenant_id: str, target_ref: str) -> ActionNotificationPolicy | None:
        del tenant_id, target_ref
        return None
