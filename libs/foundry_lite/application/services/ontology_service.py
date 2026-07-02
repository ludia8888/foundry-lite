"""Thin compatibility entrypoint for ontology workflows."""

from __future__ import annotations

from pathlib import Path

from foundry_lite.application.ports import (
    ActionTypeRow,
    ObjectTypeRow,
    OntologyApplyResult,
    OntologyCatalogResult,
    OntologyValidationResult,
    OntologyVersionRow,
    PropertyTypeRow,
    TransactionContext,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.ontology_activation_service import OntologyActivationService
from foundry_lite.application.services.ontology_catalog_service import OntologyCatalogService
from foundry_lite.application.services.ontology_lookup_service import OntologyLookupService
from foundry_lite.application.services.ontology_reindex_contract import OntologyReindexContractService
from foundry_lite.domain.context import RequestContext


class OntologyService(CoreService):
    """Compatibility facade for ontology callers while use-case services own logic."""

    required_dependencies = ()
    required_collaborators = ()

    activation_service: OntologyActivationService
    catalog_service: OntologyCatalogService
    lookup_service: OntologyLookupService
    reindex_contract_service: OntologyReindexContractService

    def __init__(
        self,
        *,
        activation_service: OntologyActivationService,
        catalog_service: OntologyCatalogService,
        lookup_service: OntologyLookupService,
        reindex_contract_service: OntologyReindexContractService,
    ) -> None:
        self.activation_service = activation_service
        self.catalog_service = catalog_service
        self.lookup_service = lookup_service
        self.reindex_contract_service = reindex_contract_service

    def apply_ontology(
        self,
        yaml_path: str | Path,
        *,
        ctx: RequestContext | None = None,
    ) -> OntologyApplyResult:
        return self.activation_service.apply_ontology(yaml_path, ctx=ctx)

    def validate_yaml_text(
        self,
        yaml_text: str,
        *,
        ctx: RequestContext | None = None,
    ) -> OntologyValidationResult:
        return self.activation_service.validate_yaml_text(yaml_text, ctx=ctx)

    def active_catalog(self, *, ctx: RequestContext | None = None) -> OntologyCatalogResult:
        return self.catalog_service.active_catalog(ctx=ctx)

    def _active_ontology_version(self, conn: TransactionContext, ctx: RequestContext) -> OntologyVersionRow:
        return self.lookup_service._active_ontology_version(conn, ctx)

    def _active_object_type(self, conn: TransactionContext, ctx: RequestContext, api_name: str) -> ObjectTypeRow:
        return self.lookup_service._active_object_type(conn, ctx, api_name)

    def _active_action_type(self, conn: TransactionContext, ctx: RequestContext, api_name: str) -> ActionTypeRow:
        return self.lookup_service._active_action_type(conn, ctx, api_name)

    def _action_type_by_id(self, conn: TransactionContext, ctx: RequestContext, action_type_id: str) -> ActionTypeRow:
        return self.lookup_service._action_type_by_id(conn, ctx, action_type_id)

    def _properties_for_object_type(
        self,
        conn: TransactionContext,
        object_type_id: str,
    ) -> list[PropertyTypeRow]:
        return list(self.lookup_service._properties_for_object_type(conn, object_type_id))

    def _complete_object_reindex_contract(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_id: str,
        reindex_key: str,
        index_run_id: str,
        dataset_version_id: str,
        completed_at: str,
    ) -> dict[str, object]:
        return self.reindex_contract_service._complete_object_reindex_contract(
            conn,
            ctx,
            object_type_id,
            reindex_key,
            index_run_id,
            dataset_version_id,
            completed_at,
        )
