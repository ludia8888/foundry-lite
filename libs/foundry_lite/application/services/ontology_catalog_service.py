"""Ontology catalog read use-case service."""

from __future__ import annotations

from foundry_lite.application.ports import OntologyCatalogResult
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.ontology_lookup_service import OntologyLookupService
from foundry_lite.application.services.ontology_protocols import OntologyRuntimeBoundary
from foundry_lite.application.services.ontology_validation import build_ontology_catalog
from foundry_lite.domain.context import RequestContext


class OntologyCatalogService(CoreService):
    """Build the active ontology catalog read model."""

    required_dependencies = ("engine",)
    required_collaborators = ("ontology_lookup_service", "runtime_service")
    ontology_lookup_service: OntologyLookupService
    runtime_service: OntologyRuntimeBoundary

    def active_catalog(self, *, ctx: RequestContext | None = None) -> OntologyCatalogResult:
        ctx = ctx or RequestContext()
        self.runtime_service._require_or_audit(ctx, "ontology:read", "ontology", "active")
        with self.engine.begin() as conn:
            active = self.ontology_lookup_service._active_ontology_version(conn, ctx)
            object_rows = self.ontology_lookup_service._object_types_for_version(conn, ctx, active["id"])
            return build_ontology_catalog(
                active=active,
                object_rows=object_rows,
                link_rows=self.ontology_lookup_service._link_types_for_version(conn, ctx, active["id"]),
                properties_by_object_id={
                    item["id"]: self.ontology_lookup_service._properties_for_object_type(conn, item["id"])
                    for item in object_rows
                },
                actions_by_object_id={
                    item["id"]: self.ontology_lookup_service._actions_for_target(conn, item["id"])
                    for item in object_rows
                },
            )
