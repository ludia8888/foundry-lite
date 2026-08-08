from __future__ import annotations

from typing import cast

import pytest
from foundry_lite.application.ports.action_notification_recipient_directory import (
    ActionNotificationRecipientDirectory,
)
from foundry_lite.application.ports.connector_registry_repository import ConnectorRegistryRepository
from foundry_lite.application.services.action_effect_authorization import validate_action_effect_targets
from foundry_lite.domain.action_runtime.action_contract import compile_action_contract
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed


class _MissingNotificationPolicy:
    def policy_for(self, *, tenant_id: str, target_ref: str) -> None:
        return None


def test_validate_action_effect_targets_rejects_unregistered_notification_policy() -> None:
    contract = compile_action_contract(
        {
            "contractVersion": 3,
            "apiName": "NotifyOrder",
            "target": "Order",
            "parameters": [],
            "rules": [],
            "effects": [
                {
                    "effectId": "notify-ops",
                    "kind": "notification",
                    "phase": "after_commit",
                    "targetRef": "notification-policy:missing",
                }
            ],
        }
    )

    with pytest.raises(ValidationFailed, match="registered recipient policy"):
        validate_action_effect_targets(
            object(),
            RequestContext(),
            cast(ConnectorRegistryRepository, object()),
            cast(ActionNotificationRecipientDirectory, _MissingNotificationPolicy()),
            contract,
        )
