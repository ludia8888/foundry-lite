"""Source bounded-context dependency bundle.

Held beside the composition root rather than inside it: ``dependencies.py`` is capped at 500
lines by the module-size gate, and this bundle grows by one field per source capability.
"""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.ports.connector_adapter import ConnectorAdapter
from foundry_lite.application.ports.connector_registry_repository import ConnectorRegistryRepository
from foundry_lite.application.ports.source_database_adapter import SourceDatabaseAdapter
from foundry_lite.application.ports.source_management_repository import SourceManagementRepository
from foundry_lite.application.ports.source_registry_repository import SourceRegistryRepository
from foundry_lite.application.ports.source_stream_adapter import SourceStreamAdapter
from foundry_lite.application.ports.virtual_table import VirtualTableReader, VirtualTableRepository


@dataclass(frozen=True)
class SourceDependencies:
    connector_adapter: ConnectorAdapter
    connector_registry_repository: ConnectorRegistryRepository
    source_registry_repository: SourceRegistryRepository
    source_management_repository: SourceManagementRepository
    source_database_adapter: SourceDatabaseAdapter
    source_stream_adapter: SourceStreamAdapter
    virtual_table_repository: VirtualTableRepository
    virtual_table_reader: VirtualTableReader
