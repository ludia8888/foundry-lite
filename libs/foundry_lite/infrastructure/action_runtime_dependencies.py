"""Focused infrastructure facade for durable Action runtime adapters."""

import os

from foundry_lite.application.ports.action_file_scanner import ActionFileScanner
from foundry_lite.application.ports.action_notification_recipient_directory import (
    ActionNotificationRecipientDirectory,
)
from foundry_lite.infrastructure.adapters.action_effect_executor import ConnectorActionEffectExecutor
from foundry_lite.infrastructure.adapters.action_file_scanner import (
    ClamAvActionFileScanner,
    ClamAvActionFileScannerConfig,
    LocalSignatureActionFileScanner,
)
from foundry_lite.infrastructure.adapters.action_function_executor import LogicDagActionFunctionExecutor
from foundry_lite.infrastructure.adapters.action_notification_recipient_directory import (
    action_notification_directory_from_json,
)
from foundry_lite.infrastructure.adapters.action_run_orchestrator import (
    LocalActionRunOrchestrator,
    TemporalActionRunConfig,
    TemporalActionRunOrchestrator,
)


def action_file_scanner_adapter(profile: str) -> ActionFileScanner:
    """Build the configured Action upload scanner with fail-closed settings."""
    if profile == "local-signature":
        return LocalSignatureActionFileScanner()
    if profile == "clamav":
        host = os.getenv("FOUNDRY_LITE_CLAMAV_HOST", "").strip()
        if not host:
            raise ValueError("FOUNDRY_LITE_CLAMAV_HOST is required for the clamav scanner profile")
        port = int(os.getenv("FOUNDRY_LITE_CLAMAV_PORT", "3310"))
        timeout = float(os.getenv("FOUNDRY_LITE_CLAMAV_TIMEOUT_SECONDS", "15"))
        return ClamAvActionFileScanner(ClamAvActionFileScannerConfig(host=host, port=port, timeout_seconds=timeout))
    raise ValueError(f"unknown Action file scanner profile: {profile}")


def action_notification_recipient_directory_adapter(*, is_protected: bool) -> ActionNotificationRecipientDirectory:
    """Build a trusted policy directory, failing closed in protected runtimes."""
    raw = os.getenv("FOUNDRY_LITE_ACTION_NOTIFICATION_POLICIES_JSON", "").strip()
    if not raw:
        if is_protected:
            raw = "{}"
        else:
            raw = (
                '{"tenant-demo":{"notification-policy:operations":'
                '{"deliveryMode":"best_effort","recipients":['
                '{"userId":"user-demo-admin","roles":["admin"]}]}}}'
            )
    return action_notification_directory_from_json(raw)


__all__ = [
    "LocalActionRunOrchestrator",
    "ConnectorActionEffectExecutor",
    "LogicDagActionFunctionExecutor",
    "TemporalActionRunConfig",
    "TemporalActionRunOrchestrator",
    "action_file_scanner_adapter",
    "action_notification_recipient_directory_adapter",
]
