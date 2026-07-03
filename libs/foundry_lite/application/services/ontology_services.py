"""Ontology bounded-context service group."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.dependencies import CoreDependencies
from foundry_lite.application.services.base import CoreService, build_service
from foundry_lite.application.services.ontology_activation_service import OntologyActivationService
from foundry_lite.application.services.ontology_branch_service import OntologyBranchService
from foundry_lite.application.services.ontology_catalog_service import OntologyCatalogService
from foundry_lite.application.services.ontology_insights_service import OntologyInsightsService
from foundry_lite.application.services.ontology_lookup_service import OntologyLookupService
from foundry_lite.application.services.ontology_proposal_service import OntologyProposalService
from foundry_lite.application.services.ontology_reindex_contract import OntologyReindexContractService
from foundry_lite.application.services.ontology_rollback_service import OntologyRollbackService
from foundry_lite.application.services.ontology_service import OntologyService


@dataclass(frozen=True)
class OntologyServices:
    """Grouped ontology services plus the stable compatibility entrypoint."""

    activation: OntologyActivationService
    branches: OntologyBranchService
    catalog: OntologyCatalogService
    entrypoint: OntologyService
    insights: OntologyInsightsService
    lookup: OntologyLookupService
    proposals: OntologyProposalService
    reindex_contract: OntologyReindexContractService
    rollback: OntologyRollbackService

    @classmethod
    def create(cls, dependencies: CoreDependencies) -> OntologyServices:
        activation = build_service(OntologyActivationService, dependencies)
        branches = build_service(OntologyBranchService, dependencies)
        catalog = build_service(OntologyCatalogService, dependencies)
        insights = build_service(OntologyInsightsService, dependencies)
        lookup = build_service(OntologyLookupService, dependencies)
        proposals = build_service(OntologyProposalService, dependencies)
        reindex_contract = build_service(OntologyReindexContractService, dependencies)
        rollback = build_service(OntologyRollbackService, dependencies)
        entrypoint = OntologyService(
            activation_service=activation,
            catalog_service=catalog,
            lookup_service=lookup,
            reindex_contract_service=reindex_contract,
            rollback_service=rollback,
        )
        return cls(
            activation=activation,
            branches=branches,
            catalog=catalog,
            entrypoint=entrypoint,
            insights=insights,
            lookup=lookup,
            proposals=proposals,
            reindex_contract=reindex_contract,
            rollback=rollback,
        )

    def items(self) -> tuple[CoreService, ...]:
        return (
            self.activation,
            self.branches,
            self.catalog,
            self.insights,
            self.lookup,
            self.proposals,
            self.reindex_contract,
            self.rollback,
        )
