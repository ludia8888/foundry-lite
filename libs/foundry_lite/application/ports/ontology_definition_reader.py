"""Application port for loading ontology definitions from external locations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract


@dataclass(frozen=True)
class OntologyDefinitionRead:
    """Location requested by the file-based ontology activation entrypoint."""

    location: str


@dataclass(frozen=True)
class OntologyDefinitionContent:
    """UTF-8 ontology definition plus integrity metadata."""

    yaml_text: str
    content_hash: str
    byte_size: int


@runtime_checkable
class OntologyDefinitionReader(Protocol):
    """Boundary that keeps ontology definition I/O outside application services."""

    @property
    def profile_name(self) -> str: ...

    def failure_contract(self) -> AdapterFailureContract:
        """Return the operator-facing failure taxonomy for this reader."""
        ...

    def read_definition(self, request: OntologyDefinitionRead) -> OntologyDefinitionContent:
        """Load one UTF-8 ontology definition."""
        ...
