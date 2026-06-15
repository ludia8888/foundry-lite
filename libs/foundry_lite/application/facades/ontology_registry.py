from __future__ import annotations

from pathlib import Path

from foundry_lite.application.ports import OntologyApplyResult
from foundry_lite.application.services.ontology_service import OntologyService
from foundry_lite.domain.context import RequestContext
from foundry_lite.observability.tracing import trace_public_methods


@trace_public_methods
class OntologyRegistry:
    """Ontology bounded context: import, validate, and activate ontology versions."""

    def __init__(self, ontology: OntologyService) -> None:
        self._ontology = ontology

    def apply(self, yaml_path: str | Path, *, ctx: RequestContext | None = None) -> OntologyApplyResult:
        return self._ontology.apply_ontology(yaml_path, ctx=ctx)
