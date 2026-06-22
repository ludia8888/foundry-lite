from __future__ import annotations

from typing import Protocol

from foundry_lite.domain.context import RequestContext


class WriteTrafficGate(Protocol):
    def _require_write_traffic_open(
        self,
        ctx: RequestContext,
        *,
        operation: str,
        resource_type: str,
        resource_id: str,
    ) -> None: ...


def require_write_open(
    runtime_service: WriteTrafficGate,
    ctx: RequestContext,
    operation: str,
    resource_type: str,
    resource_id: str,
) -> None:
    runtime_service._require_write_traffic_open(
        ctx,
        operation=operation,
        resource_type=resource_type,
        resource_id=resource_id,
    )
