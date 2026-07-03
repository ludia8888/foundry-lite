"""Thin facade entrypoints for ontology registry workflows."""

from __future__ import annotations

from pathlib import Path

from foundry_lite.application.ports import (
    OntologyApplyResult,
    OntologyCatalogResult,
    OntologyRollbackResult,
    OntologyValidationResult,
)
from foundry_lite.application.services.ontology_service import OntologyService
from foundry_lite.domain.context import RequestContext
from foundry_lite.observability.tracing import trace_public_methods


@trace_public_methods
class OntologyRegistry:
    """Ontology bounded context: import, validate, activate, and roll back ontology versions."""

    def __init__(self, ontology: OntologyService) -> None:
        self._ontology = ontology

    def apply(self, yaml_path: str | Path, *, ctx: RequestContext | None = None) -> OntologyApplyResult:
        return self._ontology.apply_ontology(yaml_path, ctx=ctx)

    def apply_text(self, yaml_text: str, *, ctx: RequestContext | None = None) -> OntologyApplyResult:
        return self._ontology.apply_ontology_text(yaml_text, ctx=ctx)

    def validate(self, yaml_text: str, *, ctx: RequestContext | None = None) -> OntologyValidationResult:
        return self._ontology.validate_yaml_text(yaml_text, ctx=ctx)

    def rollback(self, version_number: int, *, ctx: RequestContext | None = None) -> OntologyRollbackResult:
        return self._ontology.rollback_to_version(version_number, ctx=ctx)

    def catalog(self, *, ctx: RequestContext | None = None) -> OntologyCatalogResult:
        return self._ontology.active_catalog(ctx=ctx)
