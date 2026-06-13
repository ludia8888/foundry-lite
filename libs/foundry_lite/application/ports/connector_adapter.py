from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

RestAuthMode = Literal["none", "bearer", "header"]


@dataclass(frozen=True)
class RestAuthConfig:
    """Authentication settings for one REST pull source."""

    mode: RestAuthMode = "none"
    token: str | None = None
    header_name: str | None = None
    header_value: str | None = None


@dataclass(frozen=True)
class RestPaginationConfig:
    """Cursor pagination contract for a JSON REST source."""

    items_path: str = "items"
    next_cursor_path: str = "nextCursor"
    cursor_query_param: str = "cursor"
    cursor_key: str = "cursor"


@dataclass(frozen=True)
class RestSourceConfig:
    """REST source config that can be passed through the ConnectorAdapter port."""

    base_url: str
    resource_path: str
    auth: RestAuthConfig = field(default_factory=RestAuthConfig)
    pagination: RestPaginationConfig = field(default_factory=RestPaginationConfig)
    rate_limit_per_minute: int | None = None
    schema_columns: tuple[str, ...] = ()
    allow_private_network: bool = False


class ConnectorRateLimitedError(Exception):
    """Raised when a REST connector receives a rate-limit response."""


@dataclass(frozen=True)
class ConnectorSnapshotRequest:
    """Vendor-neutral request for reading one external connector snapshot."""

    connector_name: str
    resource_name: str
    tenant_id: str
    request_id: str
    cursor: Mapping[str, object] | None = None
    rest: RestSourceConfig | None = None


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
