from __future__ import annotations

from typing import Any

from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext


class ObjectLinksService(CoreService):
    required_dependencies = ("engine", "policy", "object_read_repository")
    required_collaborators = ("object_records_service",)
    object_records_service: Any

    def get_links(
        self,
        object_type_api_name: str,
        object_id: str,
        link_type_api_name: str,
        *,
        ctx: RequestContext | None = None,
    ) -> list[dict[str, Any]]:
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
            results = []
            for link in links:
                target = self.object_records_service._object_record(
                    conn, ctx, link["to_api_name"], link["to_object_id"]
                )
                if target is None:
                    continue
                results.append(
                    {
                        "linkType": link_type_api_name,
                        "from": {"objectType": object_type_api_name, "objectId": object_id},
                        "to": {
                            "objectType": link["to_api_name"],
                            "objectId": link["to_object_id"],
                            "properties": self.policy.mask_properties(
                                ctx,
                                link["to_api_name"],
                                dict(target["properties"]),
                            ),
                        },
                    }
                )
            return results
