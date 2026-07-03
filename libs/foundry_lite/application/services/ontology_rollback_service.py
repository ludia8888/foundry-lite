"""Ontology rollback use-case service (archived version -> new active version)."""

from __future__ import annotations

from foundry_lite.application.ports import (
    FunctionTypeRow,
    OntologyRollbackResult,
    OntologyVersionRow,
    TransactionContext,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.ontology_activation_service import OntologyActivationService
from foundry_lite.application.services.ontology_definition_snapshot import (
    JsonObject,
    ontology_definition_snapshot,
)
from foundry_lite.application.services.ontology_protocols import (
    OntologyRuntimeBoundary,
    require_ontology_write_open,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed


class OntologyRollbackService(CoreService):
    """Restore an archived ontology version as a brand-new active version.

    History stays append-only: rollback never flips an archived row back to
    active. It replays the archived definition through the one activation path
    so migration blocking, reindex contracts, audit, and outbox behave exactly
    like a regular apply.
    """

    required_dependencies = ("engine", "ontology_repository")
    required_collaborators = ("ontology_activation_service", "runtime_service")
    ontology_activation_service: OntologyActivationService
    runtime_service: OntologyRuntimeBoundary

    def rollback_to_version(
        self,
        version_number: int,
        *,
        ctx: RequestContext | None = None,
    ) -> OntologyRollbackResult:
        ctx = ctx or RequestContext()
        # Rollback activates a new version, so it demands the same permission as apply.
        self.runtime_service._require_or_audit(ctx, "ontology:activate", "ontology", "rollback")
        require_ontology_write_open(self.runtime_service, ctx, "rollback_ontology", "ontology", "rollback")
        with self.engine.begin() as conn:
            target = self._archived_target_version(conn, ctx, version_number)
            source = self.ontology_repository.active_ontology_version(transaction=conn, tenant_id=ctx.tenant_id)
            definition = self._definition_for_version(conn, ctx, target["id"])
            applied = self.ontology_activation_service._apply_loaded_ontology(conn, ctx, definition)
            self._record_rollback(conn, ctx, source, target, applied["ontology_version_id"], applied["version_number"])
            return {
                "ontology_version_id": applied["ontology_version_id"],
                "version_number": applied["version_number"],
                "rolled_back_from_version_number": source["version_number"] if source is not None else None,
                "rolled_back_to_version_number": target["version_number"],
                "migration_plan": applied["migration_plan"],
            }

    def _archived_target_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        version_number: int,
    ) -> OntologyVersionRow:
        target = self.ontology_repository.ontology_version_by_number(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            version_number=version_number,
        )
        if target is None:
            raise NotFound("ontology version not found", details={"versionNumber": version_number})
        if target["status"] != "archived":
            raise ValidationFailed(
                "only archived ontology versions can be rolled back to",
                details={"versionNumber": version_number, "status": target["status"]},
            )
        return target

    def _definition_for_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
    ) -> JsonObject:
        object_rows = self.ontology_repository.object_types_for_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=ontology_version_id,
        )
        action_rows = [
            action
            for row in object_rows
            for action in self.ontology_repository.actions_for_target(transaction=conn, object_type_id=row["id"])
        ]
        return ontology_definition_snapshot(
            object_rows=object_rows,
            properties_by_object_id={
                row["id"]: self.ontology_repository.properties_for_object_type(
                    transaction=conn, object_type_id=row["id"]
                )
                for row in object_rows
            },
            link_rows=self.ontology_repository.link_types_for_version(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                ontology_version_id=ontology_version_id,
            ),
            action_rows=action_rows,
            interface_rows=self.ontology_repository.interface_types_for_version(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                ontology_version_id=ontology_version_id,
            ),
            function_rows=self._function_rows_for_version(conn, ctx, ontology_version_id),
        )

    def _function_rows_for_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
    ) -> list[FunctionTypeRow]:
        return self.ontology_repository.function_types_for_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=ontology_version_id,
        )

    def _record_rollback(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        source: OntologyVersionRow | None,
        target: OntologyVersionRow,
        new_ontology_version_id: str,
        new_version_number: int,
    ) -> None:
        # A rollback is operationally distinct from a routine apply, so it gets
        # its own audit event linking the replaced and restored version numbers.
        before_ref: dict[str, object] = {
            "sourceOntologyVersionId": source["id"] if source is not None else None,
            "sourceVersionNumber": source["version_number"] if source is not None else None,
        }
        after_ref: dict[str, object] = {
            "targetOntologyVersionId": target["id"],
            "targetVersionNumber": target["version_number"],
            "newOntologyVersionId": new_ontology_version_id,
            "newVersionNumber": new_version_number,
        }
        self.runtime_service._audit(
            conn,
            ctx,
            event_type="ontology.rollback",
            resource_type="ontology_version",
            resource_id=new_ontology_version_id,
            action="rollback",
            before_ref=before_ref,
            after_ref=after_ref,
        )
