"""Application service helpers for links workflows."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol

from foundry_lite.application.ports import (
    ActionRepository,
    ObjectLinkPayload,
    ObjectLinkRow,
    ObjectRecordRow,
    OsdkResourceOperation,
    OsdkResourceType,
    TransactionContext,
)
from foundry_lite.application.services.action_log_payloads import (
    action_and_object_from_log_link,
    action_api_name_from_log_object_type,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.object_store.query_protocols import ObjectQueryOntologyLookup
from foundry_lite.application.services.object_store.row_policies import (
    RowPolicyScope,
    row_policy_scope,
    row_visible,
)
from foundry_lite.domain.context import RequestContext

# Ceiling on how many links one object may fan out to in a single response.
# Bounds both the DB read and the batched target resolution below.
MAX_LINK_FANOUT = 1_000


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
    required_dependencies = ("engine", "policy", "object_read_repository", "action_repository")
    required_collaborators = ("ontology_service", "osdk_application_service")
    ontology_service: ObjectQueryOntologyLookup
    osdk_application_service: _OsdkScopeBoundary
    action_repository: ActionRepository

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
        virtual_coordinate = action_and_object_from_log_link(link_type_api_name)
        if virtual_coordinate is not None:
            return self._action_log_links(object_type_api_name, object_id, link_type_api_name, virtual_coordinate, ctx)
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
                limit=MAX_LINK_FANOUT,
            )
            if links:
                return self._link_payloads(conn, ctx, link_type_api_name, object_type_api_name, object_id, links)
            return self._reverse_link_payloads(conn, ctx, link_type_api_name, object_type_api_name, object_id)

    def _action_log_links(
        self,
        source_type: str,
        source_id: str,
        link_type: str,
        coordinate: tuple[str, str],
        ctx: RequestContext,
    ) -> list[ObjectLinkPayload]:
        action_api_name, target_type = coordinate
        if action_api_name_from_log_object_type(source_type) != action_api_name:
            return []
        self.policy.require(ctx, "action:log:read")
        self._require_virtual_log_scopes(ctx, action_api_name, target_type)
        with self.engine.begin() as conn:
            log = self.action_repository.action_log_by_run_id(
                transaction=conn, tenant_id=ctx.tenant_id, action_run_id=source_id
            )
            if log is None or log["action_type_api_name"] != action_api_name:
                return []
            edited = self.action_repository.action_log_objects(
                transaction=conn, tenant_id=ctx.tenant_id, action_log_entry_id=log["id"]
            )
            return self._virtual_log_link_payloads(conn, ctx, source_type, source_id, link_type, target_type, edited)

    def _require_virtual_log_scopes(self, ctx: RequestContext, action_api_name: str, target_type: str) -> None:
        self.osdk_application_service.require_resource_scope(
            ctx, resource_type="action", resource_api_name=action_api_name, operation="validate"
        )
        self.osdk_application_service.require_resource_scope(
            ctx, resource_type="object", resource_api_name=target_type, operation="read"
        )

    def _virtual_log_link_payloads(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        source_type: str,
        source_id: str,
        link_type: str,
        target_type: str,
        edited: Sequence[object],
    ) -> list[ObjectLinkPayload]:
        target_ids = list(
            dict.fromkeys(
                str(item["object_id"])
                for item in edited
                if isinstance(item, dict) and item.get("object_type_api_name") == target_type
            )
        )
        rows = self.object_read_repository.object_records(
            transaction=conn, tenant_id=ctx.tenant_id, object_type_api_name=target_type, object_ids=target_ids
        )
        visible = {
            row["object_id"]: row
            for row in rows
            if row_visible(self._target_scope(conn, ctx, {}, target_type), row["properties"])
        }
        return [
            self._virtual_log_link_payload(ctx, source_type, source_id, link_type, target_type, target_id, visible)
            for target_id in target_ids
        ]

    def _virtual_log_link_payload(
        self,
        ctx: RequestContext,
        source_type: str,
        source_id: str,
        link_type: str,
        target_type: str,
        target_id: str,
        visible: dict[str, ObjectRecordRow],
    ) -> ObjectLinkPayload:
        target = visible.get(target_id)
        if target is None:
            return self._missing_target_payload(link_type, source_type, source_id, target_type, target_id)
        return self._link_payload(ctx, link_type, source_type, source_id, target)

    def _reverse_link_payloads(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        link_type_api_name: str,
        object_type_api_name: str,
        object_id: str,
    ) -> list[ObjectLinkPayload]:
        reverse_links = self.object_read_repository.active_links_to(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            link_type_api_name=link_type_api_name,
            to_api_name=object_type_api_name,
            to_object_id=object_id,
            limit=MAX_LINK_FANOUT,
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
        object_type = self.ontology_service._active_object_type(conn, ctx, object_type_api_name)
        scope = row_policy_scope(
            object_type,
            ctx.roles,
            self.ontology_service._properties_for_object_type(conn, object_type["id"]),
        )
        if scope.is_unrestricted:
            return True
        rows = self.object_read_repository.object_records(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            object_type_api_name=object_type_api_name,
            object_ids=[object_id],
        )
        record = rows[0] if rows else None
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
        targets_by_ref = self._resolve_targets(conn, ctx, links, direction)
        results: list[ObjectLinkPayload] = []
        target_scopes: dict[str, RowPolicyScope] = {}
        for link in links:
            target_type, target_id = self._target_ref(link, direction)
            target = targets_by_ref.get((target_type, target_id))
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
            object_type = self.ontology_service._active_object_type(conn, ctx, target_type)
            scopes[target_type] = row_policy_scope(
                object_type,
                ctx.roles,
                self.ontology_service._properties_for_object_type(conn, object_type["id"]),
            )
        return scopes[target_type]

    def _resolve_targets(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        links: Sequence[ObjectLinkRow],
        direction: Literal["forward", "reverse"],
    ) -> dict[tuple[str, str], ObjectRecordRow]:
        # Batch one read per distinct target type so link fan-out never triggers
        # a per-target N+1 round-trip.
        ids_by_type: dict[str, list[str]] = {}
        for link in links:
            target_type, target_id = self._target_ref(link, direction)
            ids_by_type.setdefault(target_type, []).append(target_id)
        resolved: dict[tuple[str, str], ObjectRecordRow] = {}
        for target_type, target_ids in ids_by_type.items():
            rows = self.object_read_repository.object_records(
                transaction=conn,
                tenant_id=ctx.tenant_id,
                object_type_api_name=target_type,
                object_ids=target_ids,
            )
            for row in rows:
                resolved[(row["object_type_api_name"], row["object_id"])] = row
        return resolved

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
