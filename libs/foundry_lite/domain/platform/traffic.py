"""Pure write-traffic gate decision rules."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.domain.errors import ConflictDetected


def decide_write_traffic(
    restore_mode: Mapping[str, object] | None,
    *,
    operation: str,
    resource_type: str,
    resource_id: str,
) -> None:
    if restore_mode is None:
        return
    raise ConflictDetected(
        "restore mode blocks write traffic",
        details={
            "restore_id": restore_mode["restoreId"],
            "status": restore_mode["status"],
            "operation": operation,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "is_write_traffic_paused": restore_mode["is_write_traffic_paused"],
            "is_serving_traffic_open": restore_mode["is_serving_traffic_open"],
        },
    )
