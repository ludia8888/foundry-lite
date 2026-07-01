"""Application service helpers for ontology reindex contract workflows."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports import ObjectTypeRow, OntologyRepository, TransactionContext
from foundry_lite.application.services.ontology_migration_types import (
    complete_object_reindex_config,
    pending_object_reindex_operation,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound


class OntologyReindexContractMixin:
    ontology_repository: OntologyRepository

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
        row = self._object_type_by_id(conn, ctx, object_type_id)
        operation = self._pending_object_reindex_operation(row, reindex_key)
        config = complete_object_reindex_config(
            row["config"],
            operation,
            index_run_id=index_run_id,
            dataset_version_id=dataset_version_id,
            completed_at=completed_at,
        )
        self._update_object_type_config(conn, ctx, object_type_id, reindex_key, config)
        return config

    def _object_type_by_id(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_id: str,
    ) -> ObjectTypeRow:
        row = self.ontology_repository.object_type_by_id(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_id=object_type_id,
        )
        if row is None:
            raise NotFound("object type not found", details={"object_type_id": object_type_id})
        return row

    def _pending_object_reindex_operation(
        self,
        row: ObjectTypeRow,
        reindex_key: str,
    ) -> Mapping[str, object]:
        operation = pending_object_reindex_operation(row["config"], reindex_key)
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
