"""Contract for trusted, tenant-scoped Action notification recipient policies."""

from __future__ import annotations

import pytest
from foundry_lite.infrastructure.adapters.action_notification_recipient_directory import (
    action_notification_directory_from_json,
)


def test_notification_directory_is_tenant_scoped_and_returns_current_principals() -> None:
    directory = action_notification_directory_from_json(
        """{
          "tenant-a": {
            "notification-policy:operations": {
              "deliveryMode": "best_effort",
              "recipients": [
                {"userId": "operator-1", "roles": ["ops_manager"]},
                {"userId": "viewer-1", "roles": ["viewer"]}
              ]
            }
          }
        }"""
    )

    policy = directory.policy_for(tenant_id="tenant-a", target_ref="notification-policy:operations")
    assert policy is not None
    assert policy.delivery_mode == "best_effort"
    assert [recipient.user_id for recipient in policy.recipients] == ["operator-1", "viewer-1"]
    assert directory.policy_for(tenant_id="tenant-b", target_ref="notification-policy:operations") is None


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        '{"tenant-a":{"topic:not-notification":{"recipients":[]}}}',
        '{"tenant-a":{"notification-policy:ops":{"deliveryMode":"unsafe","recipients":[]}}}',
        '{"tenant-a":{"notification-policy:ops":{"recipients":[]}}}',
        '{"tenant-a":{"notification-policy:ops":{"recipients":[{"userId":"u","roles":"viewer"}]}}}',
    ],
)
def test_notification_directory_rejects_unsafe_or_incomplete_configuration(raw: str) -> None:
    with pytest.raises(ValueError):
        action_notification_directory_from_json(raw)
