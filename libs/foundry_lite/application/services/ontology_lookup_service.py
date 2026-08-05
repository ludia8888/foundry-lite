"""Ontology lookup use-case service."""

from __future__ import annotations

from collections.abc import Sequence

from foundry_lite.application.ports import (
    ActionTypeRow,
    FunctionTypeRow,
    InterfaceTypeRow,
    LinkTypeRow,
    ObjectTypeRow,
    OntologyVersionRow,
    PropertyTypeRow,
    TransactionContext,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound


class OntologyLookupService(CoreService):
    """Read active ontology rows for Object, Action, and indexing services."""

    required_dependencies = ("ontology_repository",)
    required_collaborators = ()

    def _object_types_for_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
    ) -> Sequence[ObjectTypeRow]:
        return self.ontology_repository.object_types_for_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=ontology_version_id,
        )

    def _link_types_for_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
    ) -> Sequence[LinkTypeRow]:
        return self.ontology_repository.link_types_for_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=ontology_version_id,
        )

    def _interface_types_for_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
    ) -> Sequence[InterfaceTypeRow]:
        return self.ontology_repository.interface_types_for_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=ontology_version_id,
        )

    def _function_types_for_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
    ) -> Sequence[FunctionTypeRow]:
        return self.ontology_repository.function_types_for_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=ontology_version_id,
        )

    def _active_function_type(self, conn: TransactionContext, ctx: RequestContext, api_name: str) -> FunctionTypeRow:
        active = self._active_ontology_version(conn, ctx)
        return self._function_type_for_version(conn, ctx, active["id"], api_name)

    def _function_type_for_version(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        ontology_version_id: str,
        api_name: str,
    ) -> FunctionTypeRow:
        row = self.ontology_repository.function_type_for_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=ontology_version_id,
            api_name=api_name,
        )
        if row is None:
            raise NotFound("function type not found", details={"api_name": api_name})
        return row

    def _properties_for_object_type(
        self,
        conn: TransactionContext,
        object_type_id: str,
    ) -> Sequence[PropertyTypeRow]:
        return self.ontology_repository.properties_for_object_type(transaction=conn, object_type_id=object_type_id)

    def _actions_for_target(
        self,
        conn: TransactionContext,
        object_type_id: str,
    ) -> Sequence[ActionTypeRow]:
        return self.ontology_repository.actions_for_target(transaction=conn, object_type_id=object_type_id)

    def _active_ontology_version(self, conn: TransactionContext, ctx: RequestContext) -> OntologyVersionRow:
        row = self.ontology_repository.active_ontology_version(transaction=conn, tenant_id=ctx.tenant_id)
        if row is None:
            raise NotFound("active ontology not found")
        return row

    def _active_object_type(self, conn: TransactionContext, ctx: RequestContext, api_name: str) -> ObjectTypeRow:
        active = self._active_ontology_version(conn, ctx)
        row = self.ontology_repository.object_type_for_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=active["id"],
            api_name=api_name,
        )
        if row is None:
            raise NotFound("object type not found", details={"api_name": api_name})
        return row

    def link_type(self, conn: TransactionContext, ctx: RequestContext, api_name: str) -> LinkTypeRow:
        """Resolve one active link type by api name (satisfies CommitLinkTypeResolver for v2 actions)."""
        active = self._active_ontology_version(conn, ctx)
        for row in self._link_types_for_version(conn, ctx, active["id"]):
            if row["api_name"] == api_name:
                return row
        raise NotFound("link type not found", details={"api_name": api_name})

    def _active_action_type(self, conn: TransactionContext, ctx: RequestContext, api_name: str) -> ActionTypeRow:
        active = self._active_ontology_version(conn, ctx)
        row = self.ontology_repository.enabled_action_type_for_version(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            ontology_version_id=active["id"],
            api_name=api_name,
        )
        if row is None:
            raise NotFound("action type not found", details={"api_name": api_name})
        return row

    def _action_type_by_id(self, conn: TransactionContext, ctx: RequestContext, action_type_id: str) -> ActionTypeRow:
        row = self.ontology_repository.action_type_by_id(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            action_type_id=action_type_id,
        )
        if row is None:
            raise NotFound("action type not found", details={"action_type_id": action_type_id})
        return row

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

    def _object_type_by_id_or_none(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_id: str,
    ) -> ObjectTypeRow | None:
        return self.ontology_repository.object_type_by_id(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_id=object_type_id,
        )
