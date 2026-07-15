"""Application port for exploring and reading streaming Sources."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.connector_adapter import ConnectorNetworkRoute
from foundry_lite.application.ports.secret_provider import SecretValue
from foundry_lite.application.ports.stream_adapter import StreamAdapter


@dataclass(frozen=True)
class SourceStreamConnection:
    """Secret-safe connection settings for one streaming Source."""

    bootstrap_servers: str
    auth_scheme: str = "none"
    is_tls_enabled: bool = False
    credential: SecretValue | None = None
    network_route: ConnectorNetworkRoute | None = None


@dataclass(frozen=True)
class SourceStreamSubscription:
    """Topic and consumer settings selected by a streaming Sync."""

    stream_name: str
    topic: str
    consumer_group: str
    partition: int = 0


@dataclass(frozen=True)
class SourceStreamConnectionProbe:
    """Redacted broker evidence returned after a successful connection."""

    adapter_profile: str
    broker_count: int
    topic_count: int


@dataclass(frozen=True)
class SourceStreamTopic:
    """Topic metadata shown in Source exploration."""

    topic_name: str
    partition_count: int
    is_internal: bool


@dataclass(frozen=True)
class SourceStreamLag:
    """Broker high-watermark evidence for one topic partition."""

    earliest_offset: int
    end_offset: int
    current_offset: int | None
    lag: int


class SourceStreamAdapter(Protocol):
    """Boundary for Source-specific stream discovery and reader creation."""

    @property
    def profile_name(self) -> str: ...

    def failure_contract(self) -> AdapterFailureContract:
        """Return the adapter failure taxonomy promised by this profile."""
        ...

    def test_connection(self, connection: SourceStreamConnection) -> SourceStreamConnectionProbe:
        """Connect to the broker without returning credentials or endpoint secrets."""
        ...

    def list_topics(self, connection: SourceStreamConnection, *, limit: int) -> Sequence[SourceStreamTopic]:
        """List topics in stable order for Source exploration."""
        ...

    def open_stream(
        self,
        connection: SourceStreamConnection,
        subscription: SourceStreamSubscription,
    ) -> StreamAdapter:
        """Create a bounded reader for one Source Sync subscription."""
        ...

    def read_lag(
        self,
        connection: SourceStreamConnection,
        subscription: SourceStreamSubscription,
        *,
        current_offset: int | None,
    ) -> SourceStreamLag:
        """Compare the committed offset with the broker high watermark."""
        ...
