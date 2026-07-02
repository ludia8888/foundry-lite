"""Ontology reindex contract use-case service."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.ontology_lookup_service import OntologyLookupService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected
from foundry_lite.domain.ontology.migration_types import (
    complete_object_reindex_config,
    pending_object_reindex_operation,
)


class OntologyReindexContractService(CoreService):
    """Close required ontology object-reindex contracts after a rebuild."""

    required_dependencies = ("ontology_repository",)
    required_collaborators = ("ontology_lookup_service",)
    ontology_lookup_service: OntologyLookupService

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
        row = self.ontology_lookup_service._object_type_by_id(conn, ctx, object_type_id)
        operation = self._pending_object_reindex_operation(row["config"], reindex_key)
        config = complete_object_reindex_config(
            row["config"],
            operation,
            index_run_id=index_run_id,
            dataset_version_id=dataset_version_id,
            completed_at=completed_at,
        )
        self._update_object_type_config(conn, ctx, object_type_id, reindex_key, config)
        return config

    def _pending_object_reindex_operation(
        self,
        config: Mapping[str, object],
        reindex_key: str,
    ) -> Mapping[str, object]:
        operation = pending_object_reindex_operation(config, reindex_key)
        if operation is None:
            raise ConflictDetected(
                "ontology object reindex plan is no longer pending",
                details={"reindexKey": reindex_key},
            )
        return operation

    def _update_object_type_config(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_id: str,
        reindex_key: str,
        config: Mapping[str, object],
    ) -> None:
        updated = self.ontology_repository.update_object_type_config(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_id=object_type_id,
            config=config,
        )
        if not updated:
            raise ConflictDetected(
                "ontology object reindex completion lost its target",
                details={"reindexKey": reindex_key},
            )
