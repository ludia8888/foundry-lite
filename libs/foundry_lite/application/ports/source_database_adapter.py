"""Application port contract for source database adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract
from foundry_lite.application.ports.connector_adapter import ConnectorNetworkRoute


def _empty_network_evidence() -> dict[str, object]:
    return {}


@dataclass(frozen=True)
class SourceTableBatch:
    """Rows and schema read from a database source table."""

    rows: tuple[Mapping[str, object], ...]
    schema: Mapping[str, object]
    checkpoint: Mapping[str, object]
    network_evidence: Mapping[str, object] = field(default_factory=_empty_network_evidence)


@dataclass(frozen=True)
class SourceDatabaseConnectionProbe:
    """Redacted evidence returned after a live database connection succeeds."""

    adapter_profile: str
    database_kind: str
    driver: str
    visible_resource_count: int
    network_evidence: Mapping[str, object] = field(default_factory=_empty_network_evidence)


class SourceDatabaseAdapter(Protocol):
    """Boundary for exploring and batch-reading SQL database sources."""

    @property
    def profile_name(self) -> str: ...

    def failure_contract(self) -> AdapterFailureContract:
        """Return the adapter failure taxonomy promised by this profile."""
        ...

    def test_connection(
        self,
        database_url: str,
        *,
        network_route: ConnectorNetworkRoute | None = None,
        connection_id: str | None = None,
    ) -> SourceDatabaseConnectionProbe:
        """Open the external database and return non-secret connection evidence."""
        ...

    def list_tables(
        self,
        database_url: str,
        *,
        sample_limit: int,
        network_route: ConnectorNetworkRoute | None = None,
        connection_id: str | None = None,
    ) -> Sequence[Mapping[str, object]]:
        """List visible tables/views with optional lightweight row samples."""
        ...

    def read_table_batch(
        self,
        database_url: str,
        *,
        table_name: str,
        batch_limit: int,
        checkpoint_column: str | None = None,
        after_value: object | None = None,
        network_route: ConnectorNetworkRoute | None = None,
        connection_id: str | None = None,
    ) -> SourceTableBatch:
        """Read one small, replayable source table batch."""
        ...
