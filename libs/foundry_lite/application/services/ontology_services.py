"""Ontology bounded-context service group."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.base import CoreService, build_service
from foundry_lite.application.services.ontology_activation_service import OntologyActivationService
from foundry_lite.application.services.ontology_catalog_service import OntologyCatalogService
from foundry_lite.application.services.ontology_lookup_service import OntologyLookupService
from foundry_lite.application.services.ontology_reindex_contract import OntologyReindexContractService
from foundry_lite.application.services.ontology_service import OntologyService


@dataclass(frozen=True)
class OntologyServices:
    """Grouped ontology services plus the stable compatibility entrypoint."""

    activation: OntologyActivationService
    catalog: OntologyCatalogService
    entrypoint: OntologyService
    lookup: OntologyLookupService
    reindex_contract: OntologyReindexContractService

    @classmethod
    def create(cls, dependencies: CoreDependencies) -> OntologyServices:
        activation = build_service(OntologyActivationService, dependencies)
        catalog = build_service(OntologyCatalogService, dependencies)
        lookup = build_service(OntologyLookupService, dependencies)
        reindex_contract = build_service(OntologyReindexContractService, dependencies)
        entrypoint = OntologyService(
            activation_service=activation,
            catalog_service=catalog,
            lookup_service=lookup,
            reindex_contract_service=reindex_contract,
        )
        return cls(
            activation=activation,
            catalog=catalog,
            entrypoint=entrypoint,
            lookup=lookup,
            reindex_contract=reindex_contract,
        )

    def items(self) -> tuple[CoreService, ...]:
        return (
            self.activation,
            self.catalog,
            self.lookup,
            self.reindex_contract,
        )
