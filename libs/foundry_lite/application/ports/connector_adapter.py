from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ConnectorSnapshotRequest:
    """Vendor-neutral request for reading one external connector snapshot."""

    connector_name: str
    resource_name: str
    tenant_id: str
    request_id: str
    cursor: Mapping[str, object] | None = None


@dataclass(frozen=True)
class ConnectorSnapshot:
    """Rows and cursor metadata produced by a ConnectorAdapter snapshot."""

    connector_name: str
    resource_name: str
    rows: tuple[Mapping[str, object], ...]
    schema: Mapping[str, object]
    cursor: Mapping[str, object] | None
    source_watermark: str


class ConnectorAdapter(Protocol):
    """Scale Foundation boundary for future PostgreSQL/SaaS connectors."""

    profile_name: str

    def snapshot(self, request: ConnectorSnapshotRequest) -> ConnectorSnapshot:
        """Read a point-in-time connector snapshot."""
        ...
