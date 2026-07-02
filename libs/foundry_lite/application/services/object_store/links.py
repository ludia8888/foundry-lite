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
    required_dependencies = ("engine", "policy", "object_read_repository")
    required_collaborators = ("osdk_application_service",)
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
            results.append(self._link_payload(ctx, link_type_api_name, object_type_api_name, object_id, target))
        return results

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
