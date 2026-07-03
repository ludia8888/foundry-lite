"""Application service helpers for links workflows."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol

from foundry_lite.application.ports import (
    ObjectLinkPayload,
    ObjectLinkRow,
    ObjectRecordRow,
    OsdkResourceOperation,
    OsdkResourceType,
    TransactionContext,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.object_store.query_protocols import (
    ObjectQueryOntologyLookup,
    ObjectRecordLookup,
)
from foundry_lite.application.services.object_store.row_policies import (
    RowPolicyScope,
    row_policy_scope,
    row_visible,
)
from foundry_lite.domain.context import RequestContext


class _OsdkScopeBoundary(Protocol):
    def require_resource_scope(
        self,
        ctx: RequestContext,
        *,
        resource_type: OsdkResourceType,
        resource_api_name: str,
        operation: OsdkResourceOperation,
    ) -> None: ...


class ObjectLinksService(CoreService):
    required_dependencies = ("engine", "policy", "object_read_repository")
    required_collaborators = ("object_records_service", "ontology_service", "osdk_application_service")
    object_records_service: ObjectRecordLookup
    ontology_service: ObjectQueryOntologyLookup
    osdk_application_service: _OsdkScopeBoundary

    def get_links(
        self,
        object_type_api_name: str,
        object_id: str,
        link_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[ObjectLinkPayload]:
        ctx = ctx or RequestContext()
        self.policy.require(ctx, "object:read")
        self._require_link_read_scope(ctx, link_type_api_name)
        with self.engine.begin() as conn:
            if not self._can_traverse_from(conn, ctx, object_type_api_name, object_id):
                return []
            links = self.object_read_repository.active_links_from(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                link_type_api_name=link_type_api_name,
                from_api_name=object_type_api_name,
                from_object_id=object_id,
            )
            if links:
                return self._link_payloads(conn, ctx, link_type_api_name, object_type_api_name, object_id, links)
            reverse_links = self.object_read_repository.active_links_to(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                link_type_api_name=link_type_api_name,
                to_api_name=object_type_api_name,
                to_object_id=object_id,
            )
            return self._link_payloads(
                conn,
                ctx,
                link_type_api_name,
                object_type_api_name,
                object_id,
                reverse_links,
                direction="reverse",
            )

    def _can_traverse_from(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        object_type_api_name: str,
        object_id: str,
    ) -> bool:
        """A row hidden by its type's row policies must not anchor traversal.

        Hidden must be indistinguishable from nonexistent: a nonexistent source
        object already yields an empty link list, so hidden sources do too.
        The record read only happens for row-policy-protected types, keeping
        the common path free of an extra query.
        """
        scope = row_policy_scope(self.ontology_service._active_object_type(conn, ctx, object_type_api_name), ctx.roles)
        if scope.is_unrestricted:
            return True
        record = self.object_records_service._object_record(conn, ctx, object_type_api_name, object_id)
        return record is not None and row_visible(scope, record["properties"])

    def _require_link_read_scope(self, ctx: RequestContext, link_type_api_name: str) -> None:
        self.osdk_application_service.require_resource_scope(
            ctx,
            resource_type="link",
            resource_api_name=link_type_api_name,
            operation="read",
        )

    def _link_payloads(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        link_type_api_name: str,
        object_type_api_name: str,
        object_id: str,
        links: Sequence[ObjectLinkRow],
        *,
        direction: Literal["forward", "reverse"] = "forward",
    ) -> list[ObjectLinkPayload]:
        results: list[ObjectLinkPayload] = []
        target_scopes: dict[str, RowPolicyScope] = {}
        for link in links:
            target_type, target_id = self._target_ref(link, direction)
            target = self.object_records_service._object_record(conn, ctx, target_type, target_id)
            if target is None:
                results.append(
                    self._missing_target_payload(
                        link_type_api_name,
                        object_type_api_name,
                        object_id,
                        target_type,
                        target_id,
                    )
                )
                continue
            # Targets hidden by the TARGET type's row policies are dropped
            # entirely (no missing-target warning, which would leak existence).
            if not row_visible(self._target_scope(conn, ctx, target_scopes, target_type), target["properties"]):
                continue
            results.append(self._link_payload(ctx, link_type_api_name, object_type_api_name, object_id, target))
        return results

    def _target_scope(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        scopes: dict[str, RowPolicyScope],
        target_type: str,
    ) -> RowPolicyScope:
        """Resolve the caller's scope once per target type, not once per link row."""
        if target_type not in scopes:
            scopes[target_type] = row_policy_scope(
                self.ontology_service._active_object_type(conn, ctx, target_type), ctx.roles
            )
        return scopes[target_type]

    def _link_payload(
        self,
        ctx: RequestContext,
        link_type_api_name: str,
        object_type_api_name: str,
        object_id: str,
        target: ObjectRecordRow,
    ) -> ObjectLinkPayload:
        return {
            "linkType": link_type_api_name,
            "from": {"objectType": object_type_api_name, "objectId": object_id},
            "to": {
                "objectType": target["object_type_api_name"],
                "objectId": target["object_id"],
                "properties": self.policy.mask_properties(
                    ctx,
                    target["object_type_api_name"],
                    dict(target["properties"]),
                ),
            },
        }

    def _missing_target_payload(
        self,
        link_type_api_name: str,
        object_type_api_name: str,
        object_id: str,
        target_type: str,
        target_id: str,
    ) -> ObjectLinkPayload:
        return {
            "linkType": link_type_api_name,
            "from": {"objectType": object_type_api_name, "objectId": object_id},
            "to": {
                "objectType": target_type,
                "objectId": target_id,
                "properties": {},
                "targetMissing": True,
            },
            "warning": {
                "type": "link_target_missing",
                "message": "link target object is not available in the active object index",
            },
        }

    @staticmethod
    def _target_ref(link: ObjectLinkRow, direction: Literal["forward", "reverse"]) -> tuple[str, str]:
        if direction == "reverse":
            return link["from_api_name"], link["from_object_id"]
        return link["to_api_name"], link["to_object_id"]
