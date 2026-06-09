from __future__ import annotations

from typing import Any

from foundry_lite.application.services.base import CoreServiceMixin
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from sqlalchemy import and_, select


class ObjectLinksMixin(CoreServiceMixin):
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
            links = (
                conn.execute(
                    select(db.object_links).where(
                        and_(
                            db.object_links.c.tenant_id == ctx.tenant_id,
                            db.object_links.c.link_type_api_name == link_type_api_name,
                            db.object_links.c.from_api_name == object_type_api_name,
                            db.object_links.c.from_object_id == object_id,
                            db.object_links.c.deleted == False,  # noqa: E712
                        )
                    )
                )
                .mappings()
                .all()
            )
            results = []
            for link in links:
                target = self._object_record(conn, ctx, link["to_api_name"], link["to_object_id"])
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
