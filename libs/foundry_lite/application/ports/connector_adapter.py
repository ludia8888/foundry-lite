"""Application port contract for connector adapter."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
)

RestAuthMode = Literal["none", "bearer", "basic", "header"]
RestPaginationStrategy = Literal["cursor", "next_link", "page_number"]
ConnectorNetworkMode = Literal["direct", "agent_proxy"]
DEFAULT_REST_MAX_RESPONSE_BYTES = 5 * 1024 * 1024


def _empty_network_evidence() -> dict[str, object]:
    return {}


@dataclass(frozen=True)
class RestAuthConfig:
    """Authentication settings for one REST pull source."""

    mode: RestAuthMode = "none"
    token: str | None = None
    token_secret_ref: str | None = None
    basic_credentials: str | None = None
    basic_credentials_secret_ref: str | None = None
    header_name: str | None = None
    header_value: str | None = None
    header_value_secret_ref: str | None = None


@dataclass(frozen=True)
class RestPaginationConfig:
    """Cursor pagination contract for a JSON REST source."""

    items_path: str = "items"
    next_cursor_path: str = "nextCursor"
    cursor_query_param: str = "cursor"
    cursor_key: str = "cursor"
    strategy: RestPaginationStrategy = "cursor"
    max_pages_per_snapshot: int = 1


@dataclass(frozen=True)
class RestSourceConfig:
    """REST source config that can be passed through the ConnectorAdapter port."""

    base_url: str
    resource_path: str
    auth: RestAuthConfig = field(default_factory=RestAuthConfig)
    pagination: RestPaginationConfig = field(default_factory=RestPaginationConfig)
    rate_limit_per_minute: int | None = None
    max_response_bytes: int = DEFAULT_REST_MAX_RESPONSE_BYTES
    schema_columns: tuple[str, ...] = ()
    allow_private_network: bool = False


@dataclass(frozen=True)
class ConnectorNetworkRoute:
    """Policy-bound route used by a worker to reach one external endpoint."""

    mode: ConnectorNetworkMode
    policy_name: str | None = None
    agent_id: str | None = None
    proxy_url: str | None = None
    allowed_destinations: tuple[str, ...] = ()


class ConnectorRateLimitedError(AdapterError):
    """Raised when a REST connector receives a rate-limit response."""

    def __init__(
        self,
        retry_after: str | None,
        *,
        adapter_profile: str,
        operation: str,
        idempotency_key: str | None = None,
    ) -> None:
        message = "REST connector rate limited"
        if retry_after:
            message = f"{message}; retry_after={retry_after}"
        super().__init__(
            AdapterFailure(
                adapter_profile=adapter_profile,
                operation=operation,
                kind="rate_limited",
                is_retryable=True,
                timeout_seconds=_retry_after_seconds(retry_after),
                idempotency_key=idempotency_key,
                operator_message=message,
                details={"retry_after": retry_after} if retry_after else {},
            )
        )


@dataclass(frozen=True)
class ConnectorSnapshotRequest:
    """Vendor-neutral request for reading one external connector snapshot."""

    connector_name: str
    resource_name: str
    tenant_id: str
    request_id: str
    cursor: Mapping[str, object] | None = None
    rest: RestSourceConfig | None = None
    network_route: ConnectorNetworkRoute | None = None


@dataclass(frozen=True)
class ConnectorSnapshot:
    """Rows and cursor metadata produced by a ConnectorAdapter snapshot."""

    connector_name: str
    resource_name: str
    rows: tuple[Mapping[str, object], ...]
    schema: Mapping[str, object]
    cursor: Mapping[str, object] | None
    source_watermark: str
    network_evidence: Mapping[str, object] = field(default_factory=_empty_network_evidence)


class ConnectorAdapter(Protocol):
    """Scale Foundation boundary for future PostgreSQL/SaaS connectors."""

    @property
    def profile_name(self) -> str: ...

    def failure_contract(self) -> AdapterFailureContract:
        """Return the adapter failure taxonomy promised by this profile."""
        ...

    def snapshot(self, request: ConnectorSnapshotRequest) -> ConnectorSnapshot:
        """Read a point-in-time connector snapshot."""
        ...


def _retry_after_seconds(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
