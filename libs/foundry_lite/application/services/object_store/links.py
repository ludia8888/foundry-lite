from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from foundry_lite.application.ports import ObjectLinkPayload, ObjectLinkRow, ObjectRecordRow, TransactionContext
from foundry_lite.application.services.base import CoreService
from foundry_lite.application.services.object_store.query_protocols import ObjectRecordLookup
from foundry_lite.domain.context import RequestContext


class ObjectLinksService(CoreService):
    required_dependencies = ("engine", "policy", "object_read_repository")
    required_collaborators = ("object_records_service",)
    object_records_service: ObjectRecordLookup

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
        with self.engine.begin() as conn:
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
            results.append(self._link_payload(ctx, link_type_api_name, object_type_api_name, object_id, target))
        return results

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
